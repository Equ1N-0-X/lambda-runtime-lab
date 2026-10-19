"""ワークロードを比較対象の各言語のAPIへ同条件で送信し、測定値と実行メタデータを保存する。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import boto3
import httpx

from benchmark_runner.sigv4 import frozen_credentials, sign_headers
from benchmark_runner.workloads import Workload

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
    transport: httpx.AsyncBaseTransport | None = None


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
    return {
        "run_id": config.run_id,
        "targets": list(targets),
        "endpoints": {name: config.endpoints.get(name) for name in targets},
        "region": config.region,
        "workload": asdict(workload),
        "sign_requests": config.sign_requests,
        "dry_run": config.dry_run,
        "requests_per_target": workload.iterations,
        "warmup_per_target": workload.warmup,
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
            credentials=credentials,  # type: ignore[arg-type]
            region=region,
        )

    started_at = utc_now()
    started = time.perf_counter()
    status_code: int | None = None
    error: str | None = None
    request_id: str | None = None
    try:
        response = await client.request(workload.method, url, headers=headers, content=body)
        status_code = response.status_code
        request_id = _lambda_request_id(response)
        success = 200 <= status_code < 400
        # HTTP状態が200以上400未満なら成功、それ以外は応答を受け取れた失敗として記録する。
        if not success:
            error = f"HTTP状態 {status_code}"

    # 通信・タイムアウトなどHTTP応答を得られなかった場合は、例外種別と内容を測定失敗へ記録する。
    except httpx.HTTPError as exc:
        success = False
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000
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

    credentials = None
    # 署名ありの設定の場合だけAWS認証情報を取得し、署名なしの場合はローカルAPIへそのまま送る。
    if config.sign_requests:
        credentials = frozen_credentials(boto3.Session(region_name=config.region))
    records: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=config.timeout_seconds, transport=config.transport
    ) as client:
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

    metadata["completed_at"] = utc_now()
    metadata["sample_count"] = len(records)
    _write_jsonl(run_dir / "samples.jsonl", records)
    _write_json(run_dir / "metadata.json", metadata)
    return metadata
