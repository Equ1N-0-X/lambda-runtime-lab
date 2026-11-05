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

from benchmark_runner.build_inputs import external_base_images
from benchmark_runner.cloudwatch import collect_run
from benchmark_runner.dashboard.jobs import JobManager
from benchmark_runner.dashboard.server import LISTEN_HOST, create_server
from benchmark_runner.dashboard.settings import (
    LaunchSettings,
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
from benchmark_runner.runner import RunConfig, file_content_id, run_benchmark
from benchmark_runner.sigv4 import SigningError
from benchmark_runner.workloads import WorkloadError, apply_profile, load_workload

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TERRAFORM_DIR = ROOT / "infrastructure/terraform"
# ログ収集が期限内にそろわなかったことを表す終了コード。結果は保存済みで、再収集できる。
INCOMPLETE_COLLECTION_EXIT_CODE = 3


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
    collect.add_argument(
        "--wait-seconds",
        type=float,
        default=0,
        help="成功した要求のログがすべてそろうまで、取得し直す期限（秒）。0なら一度だけ取得する",
    )
    collect.add_argument("--dry-run", action="store_true")

    report = subparsers.add_parser("report", help="MarkdownとCSVのレポートを作成する")
    report.add_argument("--run-id", required=True)
    report.add_argument("--results-dir", type=Path, default=ROOT / "results")
    report.add_argument("--pricing", type=Path, default=ROOT / "spec/pricing.yaml")

    implementations = subparsers.add_parser(
        "implementations", help="比較対象の実装一覧を表示する（スクリプトから使う）"
    )
    implementations.add_argument(
        "--format", choices=("keys", "tsv", "pathspecs", "json"), default="keys"
    )

    inputs = subparsers.add_parser(
        "build-inputs", help="Dockerfileが参照する外部のベースイメージを一行ずつ表示する"
    )
    inputs.add_argument("--dockerfile", type=Path, required=True)
    inputs.add_argument("--build-arg", type=_key_value, action="append", default=[])

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
    # profileは本文の項目なので、本文を確定してから本文と記録の両方へ同じ値を反映する。
    workload = apply_profile(replace(workload, **overrides), args.profile)
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
    # allの場合は、ワークロードに対応する言語だけを計測する。
    targets = tuple(key for key in requested if key in supported)
    endpoints = _per_implementation(
        args.endpoint,
        env_suffixes=("API_ENDPOINT", "API_URL"),
        keys=keys,
        fallback=_terraform_output(args.terraform_dir, "api_endpoints"),
    )
    architecture, memory_mb = args.architecture, args.memory_mb
    verification: dict[str, object] = {"mode": "local"}
    # AWSへ署名して送る計測は、画面と同じ事前確認を通し、確かめた実設定を記録する。
    # 署名しないローカルAPIの確認やドライランは、AWSの照合を求めず正式な計測と区別する。
    if not args.unsigned and not args.dry_run:
        settings = _verified_settings(args, targets, endpoints)
        architecture, memory_mb = settings.architecture, settings.memory_mb
        verification = {
            "mode": "aws",
            "deployments": {key: settings.deployments.get(key) for key in targets},
        }
    config = RunConfig(
        run_id=run_id,
        targets=targets,
        unsupported_targets=tuple(key for key in keys if key not in supported),
        endpoints=endpoints,
        region=args.region,
        results_dir=args.results_dir,
        timeout_seconds=args.timeout,
        sign_requests=not args.unsigned,
        dry_run=args.dry_run,
        architecture=architecture,
        memory_mb=memory_mb,
        git_revision=_git_revision(),
        evidence={
            "verification": verification,
            "content_ids": _content_ids(args.workload_file),
        },
    )
    result = asyncio.run(run_benchmark(config, workload))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return _run_exit_code(result.get("outcomes") or {})


def _verified_settings(
    args: argparse.Namespace, targets: tuple[str, ...], endpoints: dict[str, str]
) -> LaunchSettings:
    """計測先のAPI・Lambda・実設定・版を画面と同じ方法で確かめ、記録と食い違えば止める。"""
    resolver = SettingsResolver(
        terraform_dir=args.terraform_dir or DEFAULT_TERRAFORM_DIR,
        environ={**os.environ, "AWS_REGION": args.region},
    )
    settings = resolver.resolve()
    problems = settings.missing()
    # 画面で計測を開始できない状態なら、CLIからも正式な計測を始めない。
    if problems:
        raise ValueError("計測前の確認で問題が見つかりました: " + " ".join(problems))
    # 指定した条件がLambdaの実設定と違えば、記録が実体と食い違うため止める。
    if args.architecture is not None and args.architecture != settings.architecture:
        raise ValueError(
            f"--architecture {args.architecture} が Lambda の実設定（{settings.architecture}）と"
            "異なります"
        )
    if args.memory_mb is not None and args.memory_mb != settings.memory_mb:
        raise ValueError(
            f"--memory-mb {args.memory_mb} が Lambda の実設定（{settings.memory_mb} MB）と"
            "異なります"
        )
    mismatched = [key for key in targets if endpoints.get(key) != settings.endpoints.get(key)]
    # 接続先がTerraformで管理するAPIと違えば、呼び出す先と記録するLambdaが別になる。
    if mismatched:
        raise ValueError(
            "次の言語の接続先が、Terraform で管理する API と異なります: "
            + ", ".join(mismatched)
            + "。--endpoint や <KEY>_API_ENDPOINT・<KEY>_API_URL の上書きを確認してください"
        )
    return settings


def _content_ids(workload_file: Path) -> dict[str, str | None]:
    """計測条件の元になった定義ファイルの内容識別子を返す。読めないファイルは不明とする。"""
    files = {
        "workloads": workload_file,
        "implementations": DEFAULT_REGISTRY,
        "pricing": ROOT / "spec/pricing.yaml",
        # 比較用の初期データ（DynamoDB）は、このスクリプトが決定的に生成する。
        "seed_data": ROOT / "scripts/seed-data.py",
    }
    return {name: file_content_id(path) for name, path in files.items()}


def _run_exit_code(outcomes: dict[str, dict[str, int]]) -> int:
    """全件失敗した実装があれば計測の失敗として1を返す。一部の失敗は警告して0を返す。"""
    all_failed = [name for name, counts in outcomes.items() if not counts["success_count"]]
    # 成功が一件もない実装は性能を測れていないため、後続の工程が成功を装わないよう止める。
    if all_failed:
        print(
            f"エラー: 次の言語はすべての要求が失敗しました: {', '.join(all_failed)}。"
            "結果は保存しましたが、比較には使えません。",
            file=sys.stderr,
        )
        return 1
    partial = [
        f"{name} {counts['failure_count']}件"
        for name, counts in outcomes.items()
        if counts["failure_count"]
    ]
    # 一部だけの失敗は成功分を参考値として残せるため、計測は続けて結果に注意を残す。
    if partial:
        print(
            f"警告: 失敗した要求があります（{', '.join(partial)}）。"
            "成功した要求だけの参考値として集計します。",
            file=sys.stderr,
        )
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
        wait_seconds=args.wait_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    # 期限内にログがそろわなければ、欠けたまま完了したと装わないよう終了コード3で知らせる。
    if result.get("status") == "incomplete":
        print(
            f"警告: 成功した要求のうち {result['missing_reports']} 件の実行レポートが、"
            "期限内に見つかりませんでした。時間をおいて再収集するか、未結合のまま扱ってください。",
            file=sys.stderr,
        )
        return INCOMPLETE_COLLECTION_EXIT_CODE
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
    # keysはforループ用に一行一キー、tsvはキーとアプリのディレクトリの組、
    # pathspecsは全言語のイメージの元になるファイルを一行一つのGit pathspecで、jsonは全項目を出す。
    if args.format == "keys":
        print("\n".join(implementation.key for implementation in implementations))
    elif args.format == "tsv":
        print("\n".join(f"{item.key}\t{item.app_dir}" for item in implementations))
    elif args.format == "pathspecs":
        print("\n".join(spec for item in implementations for spec in item.image_source_pathspecs))
    else:
        print(
            json.dumps(
                [
                    {
                        "key": item.key,
                        "label": item.label,
                        "app_dir": item.app_dir,
                        "artifact_paths": item.artifact_paths,
                        "test_paths": list(item.test_paths),
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
        # build-inputsならビルドの入力、それ以外のreportなら報告作成を実行する。
        if args.command == "run":
            return _run_command(args)
        if args.command == "collect":
            return _collect_command(args)
        if args.command == "ui":
            return _ui_command(args)
        if args.command == "implementations":
            return _implementations_command(args)
        if args.command == "build-inputs":
            dockerfile = args.dockerfile.read_text(encoding="utf-8")
            print("\n".join(external_base_images(dockerfile, dict(args.build_arg))))
            return 0
        return _report_command(args)
    # 署名の失敗は、API保存先の障害とは別の、計測する側の認証の問題として案内する。
    except SigningError as exc:
        print(
            f"エラー: AWSへの要求に署名できませんでした（{exc}）。"
            "AWS にログインし直してから計測し直してください。"
            "途中まで計測した結果は保存されていますが、比較には使えません。",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, WorkloadError, SettingsUnavailable) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
