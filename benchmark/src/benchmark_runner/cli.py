"""ベンチマークの実行・ログ収集・レポート作成を一つのCLIとして提供する。"""

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
    """結果の再現性に使う現在のGitコミットを取得し、取得不能ならNoneを返す。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    # Gitが未導入または作業場所がリポジトリでない場合は、計測自体を止めずリビジョン不明とする。
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _json_payload(value: str) -> object:
    """@で始まる値はファイル、それ以外は直接指定JSONとして解析する。"""
    try:
        # 先頭が@の場合だけ後続文字列をファイルパスとし、内容をJSONとして読み込む。
        if value.startswith("@"):
            return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
        return json.loads(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"JSON payloadが不正です: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    """三つの操作と、それぞれに必要な接続先・実行条件・出力先を定義する。"""
    parser = argparse.ArgumentParser(prog="benchmark-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="ベンチマークワークロードを実行する")
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

    collect = subparsers.add_parser("collect", help="Lambdaログを収集して測定値と結合する")
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--results-dir", type=Path, default=ROOT / "results")
    collect.add_argument("--region", default=os.getenv("AWS_REGION", "ap-northeast-1"))
    collect.add_argument("--python-log-group", default=os.getenv("PYTHON_LOG_GROUP"))
    collect.add_argument("--rust-log-group", default=os.getenv("RUST_LOG_GROUP"))
    collect.add_argument("--strategy", choices=("auto", "insights", "filter"), default="auto")
    collect.add_argument("--dry-run", action="store_true")

    report = subparsers.add_parser("report", help="MarkdownとCSVのレポートを作成する")
    report.add_argument("--run-id", required=True)
    report.add_argument("--results-dir", type=Path, default=ROOT / "results")
    report.add_argument("--pricing", type=Path, default=ROOT / "spec/pricing.yaml")
    return parser


def _run_command(args: argparse.Namespace) -> int:
    """定義済みワークロードへCLI上書きを適用し、検証後に計測を実行する。"""
    workload = load_workload(args.workload_file, args.workload)
    overrides = {}
    # 各CLI引数が明示された場合だけ、YAML定義の該当値を上書きする。
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
    # 反復・並行数は正数、ウォームアップは0以上の場合だけ実行可能な条件として受け付ける。
    if workload.iterations <= 0 or workload.client_concurrency <= 0 or workload.warmup < 0:
        raise ValueError("iterationsとconcurrencyは正数、warmupは0以上で指定してください")

    # run-idがautoなら新しいUUIDを生成し、明示値なら複数工程で共有できるようそのまま使う。
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
    """設定済み実装のロググループを収集対象にし、保存済み測定と相関する。"""
    log_groups = {
        name: value
        for name, value in (
            ("python", args.python_log_group),
            ("rust", args.rust_log_group),
        )
        if value
    }
    # Python/Rustのどちらのロググループもない場合は収集できないため、AWS呼び出し前に停止する。
    if not log_groups:
        raise ValueError("CloudWatchロググループを一つ以上設定してください")
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
    """指定実行IDの測定値からMarkdown・CSVレポートを生成し、保存先を表示する。"""
    markdown, csv_path = generate_report(
        run_id=args.run_id,
        results_dir=args.results_dir,
        pricing_path=args.pricing,
    )
    print(json.dumps({"markdown": str(markdown), "csv": str(csv_path)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """サブコマンドを振り分け、利用者が修正可能なエラーを日本語で標準エラーへ表示する。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # runなら計測、collectならログ収集、それ以外の必須選択肢reportなら報告作成を実行する。
        if args.command == "run":
            return _run_command(args)
        if args.command == "collect":
            return _collect_command(args)
        return _report_command(args)
    except (OSError, ValueError, WorkloadError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
