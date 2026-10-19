"""ベンチマークの実行・ログ収集・レポート作成を一つのCLIとして提供する。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import boto3

from benchmark_runner.cloudwatch import collect_run
from benchmark_runner.dashboard.jobs import JobManager
from benchmark_runner.dashboard.server import LISTEN_HOST, create_server
from benchmark_runner.dashboard.settings import (
    SettingsResolver,
    SettingsUnavailable,
    read_terraform_outputs,
)
from benchmark_runner.implementations import (
    DEFAULT_REGISTRY,
    load_implementations,
    resolve_targets,
)
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


def _key_value(value: str) -> tuple[str, str]:
    """実装キーと値を「key=value」の形で受け取り、組にして返す。"""
    key, separator, rest = value.partition("=")
    # 区切りがない、またはキーか値が空なら、どの実装の値か特定できない。
    if not separator or not key or not rest:
        raise argparse.ArgumentTypeError(f"実装キー=値 の形で指定してください: {value}")
    return key, rest


def _per_implementation(
    pairs: list[tuple[str, str]] | None,
    *,
    env_suffixes: tuple[str, ...],
    keys: tuple[str, ...],
    fallback: dict[str, str] | None = None,
) -> dict[str, str]:
    """実装ごとの値を、引数、環境変数（<KEY>_<接尾辞>）、Terraform出力の順に集める。"""
    explicit = dict(pairs or [])
    unknown = sorted(set(explicit) - set(keys))
    # 一覧にない実装キーは誤記の可能性が高いため、値を黙って捨てずに止める。
    if unknown:
        raise ValueError(f"実装一覧にないキーが指定されています: {', '.join(unknown)}")
    values = {}
    for key in keys:
        candidates = [
            explicit.get(key),
            *(os.getenv(f"{key.upper()}_{suffix}") for suffix in env_suffixes),
            (fallback or {}).get(key),
        ]
        value = next((candidate for candidate in candidates if candidate), None)
        # 値がない実装は対応表へ入れず、利用側で不足として扱えるようにする。
        if value:
            values[key] = value
    return values


def _terraform_output(terraform_dir: Path | None, name: str) -> dict[str, str]:
    """--terraform-dir が指定された場合だけ、実装キーごとのTerraform出力を読む。"""
    # 指定がなければ、引数と環境変数だけで値を決める（ローカルAPIの計測など）。
    if terraform_dir is None:
        return {}
    return read_terraform_outputs(terraform_dir).get(name) or {}


def build_parser() -> argparse.ArgumentParser:
    """各操作と、それぞれに必要な接続先・実行条件・出力先を定義する。"""
    parser = argparse.ArgumentParser(prog="benchmark-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="ベンチマークワークロードを実行する")
    run.add_argument(
        "--target", default="all", help="all、または実装キーのカンマ区切り（例: python,rust）"
    )
    run.add_argument("--workload", required=True)
    run.add_argument("--workload-file", type=Path, default=ROOT / "spec/workloads.yaml")
    run.add_argument("--run-id", default="auto")
    run.add_argument(
        "--endpoint",
        type=_key_value,
        action="append",
        help="実装キー=APIのURL。未指定の実装は環境変数 <KEY>_API_ENDPOINT・<KEY>_API_URL を使う",
    )
    run.add_argument(
        "--terraform-dir",
        type=Path,
        help="引数・環境変数にない接続先を、このTerraformの出力（api_endpoints）から読む",
    )
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
    collect.add_argument(
        "--log-group",
        type=_key_value,
        action="append",
        help="実装キー=ロググループ名。未指定の実装は環境変数 <KEY>_LOG_GROUP を使う",
    )
    collect.add_argument(
        "--terraform-dir",
        type=Path,
        help="引数・環境変数にないロググループを、このTerraformの出力（lambda_names）から求める",
    )
    collect.add_argument("--strategy", choices=("auto", "insights", "filter"), default="auto")
    collect.add_argument("--dry-run", action="store_true")

    report = subparsers.add_parser("report", help="MarkdownとCSVのレポートを作成する")
    report.add_argument("--run-id", required=True)
    report.add_argument("--results-dir", type=Path, default=ROOT / "results")
    report.add_argument("--pricing", type=Path, default=ROOT / "spec/pricing.yaml")

    implementations = subparsers.add_parser(
        "implementations", help="比較対象の実装一覧を表示する（スクリプトから使う）"
    )
    implementations.add_argument("--format", choices=("keys", "tsv", "json"), default="keys")

    ui = subparsers.add_parser("ui", help="計測結果の閲覧と計測開始の画面を手元で起動する")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--workload-file", type=Path, default=ROOT / "spec/workloads.yaml")
    ui.add_argument("--terraform-dir", type=Path, default=ROOT / "infrastructure/terraform")
    ui.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ui.add_argument("--pricing", type=Path, default=ROOT / "spec/pricing.yaml")
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
    keys = tuple(implementation.key for implementation in load_implementations())
    supported = workload.supported(keys)
    requested = resolve_targets(args.target, keys)
    unsupported = [key for key in requested if key not in supported]
    # 言語を明示した場合に未対応の言語が含まれていれば、黙って外さず指定の誤りとして止める。
    if args.target not in ("all", "both") and unsupported:
        raise ValueError(
            f"ワークロード {workload.name} は次の言語に未対応です: {', '.join(unsupported)}"
        )
    config = RunConfig(
        run_id=run_id,
        # allの場合は、ワークロードに対応する言語だけを計測する。
        targets=tuple(key for key in requested if key in supported),
        unsupported_targets=tuple(key for key in keys if key not in supported),
        endpoints=_per_implementation(
            args.endpoint,
            env_suffixes=("API_ENDPOINT", "API_URL"),
            keys=keys,
            fallback=_terraform_output(args.terraform_dir, "api_endpoints"),
        ),
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
    keys = tuple(implementation.key for implementation in load_implementations())
    deployed = {
        key: f"/aws/lambda/{name}"
        for key, name in _terraform_output(args.terraform_dir, "lambda_names").items()
    }
    log_groups = _per_implementation(
        args.log_group, env_suffixes=("LOG_GROUP",), keys=keys, fallback=deployed
    )
    # どの実装のロググループもない場合は収集できないため、AWS呼び出し前に停止する。
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


def _implementations_command(args: argparse.Namespace) -> int:
    """実装一覧を、シェルで扱いやすい行形式、または全項目のJSONで出力する。"""
    implementations = load_implementations(DEFAULT_REGISTRY)
    # keysはforループ用に一行一キー、tsvはキーとアプリのディレクトリの組、jsonは全項目を出す。
    if args.format == "keys":
        print("\n".join(implementation.key for implementation in implementations))
    elif args.format == "tsv":
        print("\n".join(f"{item.key}\t{item.app_dir}" for item in implementations))
    else:
        print(
            json.dumps(
                [
                    {
                        "key": item.key,
                        "label": item.label,
                        "app_dir": item.app_dir,
                        "artifact_paths": item.artifact_paths,
                    }
                    for item in implementations
                ],
                ensure_ascii=False,
            )
        )
    return 0


def _ui_command(args: argparse.Namespace) -> int:
    """画面のサーバーを起動し、Ctrl+Cで停止されるまで待ち受ける。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # 接続先と比較条件はTerraformとLambdaの実設定から求め、画面からは変更させない。
    resolver = SettingsResolver(terraform_dir=args.terraform_dir)
    jobs = JobManager(
        settings_provider=resolver.resolve,
        workload_file=args.workload_file,
        results_dir=args.results_dir,
        pricing_path=args.pricing,
    )
    server = create_server(
        port=args.port,
        results_dir=args.results_dir,
        pricing_path=args.pricing,
        jobs=jobs,
    )
    print(f"計測結果の画面: http://{LISTEN_HOST}:{args.port}/ （Ctrl+Cで停止）", flush=True)
    try:
        server.serve_forever()
    # Ctrl+Cは利用者による通常の停止操作なので、エラー扱いせず正常終了する。
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """サブコマンドを振り分け、利用者が修正可能なエラーを日本語で標準エラーへ表示する。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # runなら計測、collectならログ収集、uiなら閲覧画面、implementationsなら実装一覧、
        # それ以外のreportなら報告作成を実行する。
        if args.command == "run":
            return _run_command(args)
        if args.command == "collect":
            return _collect_command(args)
        if args.command == "ui":
            return _ui_command(args)
        if args.command == "implementations":
            return _implementations_command(args)
        return _report_command(args)
    except (OSError, ValueError, WorkloadError, SettingsUnavailable) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
