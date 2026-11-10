"""ワークロードを比較対象の各言語のAPIへ同条件で送信し、測定値と実行メタデータを保存する。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import boto3
import httpx

from benchmark_runner.cold_start import ColdStartError
from benchmark_runner.response_contract import validate_success_response
from benchmark_runner.sigv4 import SigningError, credential_source, sign_headers, snapshot
from benchmark_runner.workloads import Workload, experiment_id

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    # 計測する実装のキー。実装一覧の表示順に並べ、この順で計測する。
    targets: tuple[str, ...]
    endpoints: dict[str, str]
    region: str
    results_dir: Path
    timeout_seconds: float = 30.0
    sign_requests: bool = True
    dry_run: bool = False
    architecture: str | None = None
    memory_mb: int | None = None
    git_revision: str | None = None
    # ワークロードが未対応のため計測しない実装のキー。結果で「未対応」と欠損を区別するために残す。
    unsupported_targets: tuple[str, ...] = ()
    transport: httpx.AsyncBaseTransport | None = None
    # 計測先の確認結果や定義ファイルの内容識別子など、runの証跡として保存する追加の記録。
    evidence: dict[str, Any] = field(default_factory=dict)
    # コールドスタート計測で設定を更新するLambda関数名（実装キーごと）と、更新を行う部品。
    function_names: dict[str, str] = field(default_factory=dict)
    cold_starter: Any | None = None


def utc_now() -> str:
    """実行環境のタイムゾーンに依存しないISO 8601 UTC時刻を返す。"""
    return datetime.now(UTC).isoformat()


def validate_run_id(run_id: str) -> None:
    """実行IDがヘッダーと安全な結果ディレクトリ名の両方で利用できる形式か確認する。"""

    # 実行IDが英数字開始かつ許可文字・最大長に一致しない場合は、ファイル作成前に拒否する。
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run IDは英数字で始め、英数字と '.', '_'、'-' だけで指定してください")


def _request_body(workload: Workload) -> bytes | None:
    """本文なしの要求はNone、本文ありの要求は安定したUTF-8 JSONへ変換する。"""

    # ワークロードに本文がない場合はContent-Length付き空JSONを作らず、本文なしで送信する。
    if workload.body is None:
        return None
    return json.dumps(
        workload.body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def content_id(value: bytes) -> str:
    """内容が同じかを照合するための識別子（SHA-256）を返す。"""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_content_id(path: Path) -> str | None:
    """ファイルの内容識別子を返す。読めなければ推測せず不明（None）とする。"""
    try:
        return content_id(path.read_bytes())
    except OSError:
        return None


def build_plan(config: RunConfig, workload: Workload) -> dict[str, Any]:
    """実行前に対象と接続先を検証し、保存可能な実行計画へ変換する。"""
    targets = config.targets
    # 計測対象が一つもなければ、保存しても比較できない空の結果になるため開始しない。
    if not targets:
        raise ValueError("計測する実装を一つ以上指定してください")
    # ドライランでない場合だけ、選択した全実装に接続先が設定されていることを要求する。
    if not config.dry_run:
        missing = [name for name in targets if not config.endpoints.get(name)]

        # 接続先が不足している場合は、一部実装だけを測定して比較結果を不完全にしない。
        if missing:
            raise ValueError(f"接続先が設定されていません: {', '.join(missing)}")
        # コールドスタート計測は関数の設定を更新するため、全実装の関数名と更新部品を要求する。
        if workload.cold_start:
            unknown = [name for name in targets if not config.function_names.get(name)]
            if unknown or config.cold_starter is None:
                raise ValueError(
                    "コールドスタートの計測には、AWS上のLambda関数名が必要です"
                    f"（不足: {', '.join(unknown) or '更新の設定'}）"
                )
    return {
        "run_id": config.run_id,
        "targets": list(targets),
        "unsupported_targets": list(config.unsupported_targets),
        "endpoints": {name: config.endpoints.get(name) for name in targets},
        "region": config.region,
        "workload": asdict(workload),
        "sign_requests": config.sign_requests,
        "dry_run": config.dry_run,
        "requests_per_target": workload.iterations,
        "warmup_per_target": workload.warmup,
        # 処理の内容と同時要求数の識別子。料金の根拠を選ぶとき、同じ実験かを照合するために使う。
        "experiment_id": experiment_id(workload),
        **config.evidence,
    }


def _lambda_request_id(response: httpx.Response) -> str | None:
    """API Gatewayヘッダーを優先し、なければJSON本文からLambdaリクエストIDを探す。"""
    for name in ("x-amzn-requestid", "x-amz-request-id"):
        value = response.headers.get(name)
        # 候補ヘッダーに空でない値があれば、CloudWatchログとの相関IDとして採用する。
        if value:
            return value
    try:
        document = response.json()
    # 応答がJSONでない場合は本文からIDを取得できないため、相関なしとしてNoneを返す。
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    # JSONルートがオブジェクトの場合だけ、互換候補となる二つのキーを順に確認する。
    if isinstance(document, dict):
        for name in ("lambda_request_id", "request_id"):
            value = document.get(name)
            # 候補値が空でない文字列の場合だけ相関IDとして採用する。
            if isinstance(value, str) and value:
                return value
    return None


async def _send_one(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    implementation: str,
    workload: Workload,
    run_id: str,
    request_index: int,
    region: str,
    credentials: object | None,
) -> dict[str, Any]:
    """一回のHTTP要求を送信し、成功可否・所要時間・相関IDを一レコードへまとめる。"""
    url = urljoin(endpoint.rstrip("/") + "/", workload.path.lstrip("/"))
    body = _request_body(workload)
    headers = {
        "Accept": "application/json",
        "X-Benchmark-Run-Id": run_id,
        "X-Benchmark-Request-Index": str(request_index),
    }
    # 本文がある場合だけJSONのContent-Typeを付け、GETなど本文なし要求には付けない。
    if body is not None:
        headers["Content-Type"] = "application/json"
    # 認証情報がある場合だけSigV4署名し、明示的な未署名モードでは元のヘッダーを使う。
    if credentials is not None:
        headers = sign_headers(
            method=workload.method,
            url=url,
            headers=headers,
            body=body,
            credentials=snapshot(credentials),
            region=region,
        )

    started_at = utc_now()
    started = time.perf_counter()
    status_code: int | None = None
    error: str | None = None
    request_id: str | None = None
    try:
        response = await client.request(workload.method, url, headers=headers, content=body)
    # 通信・タイムアウトなどHTTP応答を得られなかった場合は、例外種別と内容を測定失敗へ記録する。
    except httpx.HTTPError as exc:
        response = None
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000

    # 応答本文の確認は計測区間の外で行い、確認の処理時間を応答時間へ含めない。
    if response is not None:
        status_code = response.status_code
        request_id = _lambda_request_id(response)
        error = validate_success_response(workload.path, status_code, response.content)
    success = response is not None and error is None
    return {
        "run_id": run_id,
        "implementation": implementation,
        "workload": workload.name,
        "profile": workload.profile,
        "request_index": request_index,
        "started_at": started_at,
        "latency_ms": round(latency_ms, 6),
        "status_code": status_code,
        "success": success,
        # 成功を状態コードだけでなく本文の内容でも確かめたことを示す。古い計測との区別に使う。
        "response_checked": True,
        "error": error,
        "lambda_request_id": request_id,
    }


async def _execute_target(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    implementation: str,
    workload: Workload,
    config: RunConfig,
    credentials: object | None,
) -> list[dict[str, Any]]:
    """一実装への同時要求数を制限し、ウォームアップ後の測定結果だけを返す。"""
    semaphore = asyncio.Semaphore(workload.client_concurrency)

    async def invoke(index: int) -> dict[str, Any]:
        async with semaphore:
            return await _send_one(
                client,
                endpoint=endpoint,
                implementation=implementation,
                workload=workload,
                run_id=config.run_id,
                request_index=index,
                region=config.region,
                credentials=credentials,
            )

    # warmupが1件以上なら負の添字で事前要求し、結果ファイルには含めず実行環境だけを温める。
    if workload.warmup:
        await asyncio.gather(*(invoke(-index) for index in range(1, workload.warmup + 1)))
    return list(
        await asyncio.gather(*(invoke(index) for index in range(1, workload.iterations + 1)))
    )


async def _execute_cold_target(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    implementation: str,
    workload: Workload,
    config: RunConfig,
    credentials: object | None,
    records: list[dict[str, Any]],
) -> None:
    """要求の前に毎回新しい実行環境を起こし、すべての要求をコールドスタートとして測る。

    中断されてもそこまでの結果を残せるよう、測った要求は一件ずつ records へ追加する。
    """
    function_name = config.function_names[implementation]
    starter = config.cold_starter
    try:
        for index in range(1, workload.iterations + 1):
            # 設定の更新は数秒かかるため別スレッドで待ち、他の言語の計測を止めない。
            await asyncio.to_thread(starter.force, function_name)
            record = await _send_one(
                client,
                endpoint=endpoint,
                implementation=implementation,
                workload=workload,
                run_id=config.run_id,
                request_index=index,
                region=config.region,
                credentials=credentials,
            )
            # 設定の更新でコールドスタートを起こした要求であることを記録する。
            # 実際にコールドスタートだったかは、ログ収集後にLambdaの実行レポートで確かめる。
            record["forced_cold_start"] = True
            records.append(record)
    finally:
        # 中断・失敗の場合も、計測用の設定をTerraformで管理する状態へ戻す。
        # 取り消しで待ちが中断されないよう、戻す処理は同期的に実行する。
        starter.restore(function_name)


def _write_json(path: Path, value: object) -> None:
    """人が確認できる整形済みJSONとしてメタデータを書き込む。"""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """測定一件を一行にして、追記・逐次処理しやすいJSONL形式で書き込む。"""
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


async def run_benchmark(config: RunConfig, workload: Workload) -> dict[str, Any]:
    """実行計画を確定し、各実装の測定と再現用メタデータを同じ実行ディレクトリへ保存する。"""
    validate_run_id(config.run_id)
    plan = build_plan(config, workload)
    # ドライランではディレクトリ作成やHTTP送信を行わず、検証済み計画だけを返す。
    if config.dry_run:
        return plan

    run_dir = config.results_dir / "raw" / config.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata: dict[str, Any] = {
        **plan,
        "started_at": utc_now(),
        "completed_at": None,
        "architecture": config.architecture,
        "memory_mb": config.memory_mb,
        "git_revision": config.git_revision,
    }
    _write_json(run_dir / "metadata.json", metadata)

    records: list[dict[str, Any]] = []
    try:
        credentials = None
        # 署名ありの設定の場合だけAWS認証情報を取得し、署名なしの場合はローカルAPIへそのまま送る。
        if config.sign_requests:
            credentials = credential_source(boto3.Session(region_name=config.region))
        async with httpx.AsyncClient(
            timeout=config.timeout_seconds, transport=config.transport
        ) as client:
            # コールドスタート計測は言語ごとに別の関数を更新するため、全言語を同時に進めて
            # 待ち時間を短くする。通常の計測は言語ごとに順に行い、互いの負荷を重ねない。
            if workload.cold_start:
                try:
                    # 一つの言語が失敗したら、他の言語も止めて設定を戻してから中断する。
                    async with asyncio.TaskGroup() as group:
                        for implementation in config.targets:
                            group.create_task(
                                _execute_cold_target(
                                    client=client,
                                    endpoint=config.endpoints[implementation],
                                    implementation=implementation,
                                    workload=workload,
                                    config=config,
                                    credentials=credentials,
                                    records=records,
                                )
                            )
                except ExceptionGroup as group_error:
                    raise group_error.exceptions[0] from group_error
                # 言語ごとに同時に測った結果を、実装一覧の順・要求番号の順にそろえる。
                order = {name: position for position, name in enumerate(config.targets)}
                records.sort(
                    key=lambda record: (order[record["implementation"]], record["request_index"])
                )
            else:
                for implementation in config.targets:
                    records.extend(
                        await _execute_target(
                            client=client,
                            endpoint=config.endpoints[implementation],
                            implementation=implementation,
                            workload=workload,
                            config=config,
                            credentials=credentials,
                        )
                    )
    # 署名できなくなった場合や、コールドスタートを起こせなかった場合は、そこまでの結果を
    # 中断したrunとして残し、呼び出し元へ知らせる。
    except (SigningError, ColdStartError) as exc:
        reason = "署名" if isinstance(exc, SigningError) else "Lambda の設定の更新"
        metadata["completed_at"] = utc_now()
        metadata["aborted"] = f"{reason}に失敗したため計測を中断しました: {exc}"
        metadata["sample_count"] = len(records)
        _write_jsonl(run_dir / "samples.jsonl", records)
        _write_json(run_dir / "metadata.json", metadata)
        raise

    metadata["completed_at"] = utc_now()
    metadata["sample_count"] = len(records)
    # 実装ごとの成功・失敗件数を残し、全件失敗した計測を工程の失敗として扱えるようにする。
    metadata["outcomes"] = {
        implementation: {
            "success_count": sum(
                1
                for record in records
                if record["implementation"] == implementation and record["success"]
            ),
            "failure_count": sum(
                1
                for record in records
                if record["implementation"] == implementation and not record["success"]
            ),
        }
        for implementation in config.targets
    }
    _write_jsonl(run_dir / "samples.jsonl", records)
    _write_json(run_dir / "metadata.json", metadata)
    return metadata
