from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import boto3

from benchmark_runner.cloudwatch import collect_run
from benchmark_runner.report import generate_report
from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.workloads import WorkloadError, load_workload

ROOT = Path(__file__).resolve().parents[3]


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _json_payload(value: str) -> object:
    try:
        if value.startswith("@"):
            return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
        return json.loads(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON payload: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run a benchmark workload")
    run.add_argument("--target", choices=("python", "rust", "both"), default="both")
    run.add_argument("--workload", required=True)
    run.add_argument("--workload-file", type=Path, default=ROOT / "spec/workloads.yaml")
    run.add_argument("--run-id", default="auto")
    run.add_argument("--python-endpoint", default=os.getenv("PYTHON_API_ENDPOINT"))
    run.add_argument("--rust-endpoint", default=os.getenv("RUST_API_ENDPOINT"))
    run.add_argument("--region", default=os.getenv("AWS_REGION", "ap-northeast-1"))
    run.add_argument("--results-dir", type=Path, default=ROOT / "results")
    run.add_argument("--iterations", type=int)
    run.add_argument("--concurrency", type=int)
    run.add_argument("--warmup", type=int)
    run.add_argument("--payload", type=_json_payload)
    run.add_argument("--profile")
    run.add_argument("--timeout", type=float, default=30.0)
    run.add_argument("--unsigned", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--architecture", choices=("arm64", "x86_64"))
    run.add_argument("--memory-mb", type=int)

    collect = subparsers.add_parser("collect", help="collect and correlate Lambda logs")
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--results-dir", type=Path, default=ROOT / "results")
    collect.add_argument("--region", default=os.getenv("AWS_REGION", "ap-northeast-1"))
    collect.add_argument("--python-log-group", default=os.getenv("PYTHON_LOG_GROUP"))
    collect.add_argument("--rust-log-group", default=os.getenv("RUST_LOG_GROUP"))
    collect.add_argument("--strategy", choices=("auto", "insights", "filter"), default="auto")
    collect.add_argument("--dry-run", action="store_true")

    report = subparsers.add_parser("report", help="generate Markdown and CSV reports")
    report.add_argument("--run-id", required=True)
    report.add_argument("--results-dir", type=Path, default=ROOT / "results")
    report.add_argument("--pricing", type=Path, default=ROOT / "spec/pricing.yaml")
    return parser


def _run_command(args: argparse.Namespace) -> int:
    workload = load_workload(args.workload_file, args.workload)
    overrides = {}
    if args.iterations is not None:
        overrides["iterations"] = args.iterations
    if args.concurrency is not None:
        overrides["client_concurrency"] = args.concurrency
    if args.warmup is not None:
        overrides["warmup"] = args.warmup
    if args.payload is not None:
        overrides["body"] = args.payload
    if args.profile is not None:
        overrides["profile"] = args.profile
    workload = replace(workload, **overrides)
    if workload.iterations <= 0 or workload.client_concurrency <= 0 or workload.warmup < 0:
        raise ValueError("iterations/concurrency must be positive and warmup must be non-negative")

    run_id = str(uuid.uuid4()) if args.run_id == "auto" else args.run_id
    config = RunConfig(
        run_id=run_id,
        target=args.target,
        endpoints={
            "python": args.python_endpoint,
            "rust": args.rust_endpoint,
        },
        region=args.region,
        results_dir=args.results_dir,
        timeout_seconds=args.timeout,
        sign_requests=not args.unsigned,
        dry_run=args.dry_run,
        architecture=args.architecture,
        memory_mb=args.memory_mb,
        git_revision=_git_revision(),
    )
    result = asyncio.run(run_benchmark(config, workload))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _collect_command(args: argparse.Namespace) -> int:
    log_groups = {
        name: value
        for name, value in (
            ("python", args.python_log_group),
            ("rust", args.rust_log_group),
        )
        if value
    }
    if not log_groups:
        raise ValueError("at least one CloudWatch log group must be configured")
    client = boto3.client("logs", region_name=args.region)
    result = collect_run(
        run_id=args.run_id,
        results_dir=args.results_dir,
        log_groups=log_groups,
        logs_client=client,
        strategy=args.strategy,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _report_command(args: argparse.Namespace) -> int:
    markdown, csv_path = generate_report(
        run_id=args.run_id,
        results_dir=args.results_dir,
        pricing_path=args.pricing,
    )
    print(json.dumps({"markdown": str(markdown), "csv": str(csv_path)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run_command(args)
        if args.command == "collect":
            return _collect_command(args)
        return _report_command(args)
    except (OSError, ValueError, WorkloadError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
