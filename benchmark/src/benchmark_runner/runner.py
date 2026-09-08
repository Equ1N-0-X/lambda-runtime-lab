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
    target: str
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
    return datetime.now(UTC).isoformat()


def selected_targets(target: str) -> tuple[str, ...]:
    if target == "both":
        return ("python", "rust")
    if target in {"python", "rust"}:
        return (target,)
    raise ValueError("target must be python, rust, or both")


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run ID may contain only letters, numbers, '.', '_' and '-'")


def _request_body(workload: Workload) -> bytes | None:
    if workload.body is None:
        return None
    return json.dumps(
        workload.body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def build_plan(config: RunConfig, workload: Workload) -> dict[str, Any]:
    targets = selected_targets(config.target)
    if not config.dry_run:
        missing = [name for name in targets if not config.endpoints.get(name)]
        if missing:
            raise ValueError(f"endpoint not configured for: {', '.join(missing)}")
    return {
        "run_id": config.run_id,
        "target": config.target,
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
    for name in ("x-amzn-requestid", "x-amz-request-id"):
        value = response.headers.get(name)
        if value:
            return value
    try:
        document = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(document, dict):
        for name in ("lambda_request_id", "request_id"):
            value = document.get(name)
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
    url = urljoin(endpoint.rstrip("/") + "/", workload.path.lstrip("/"))
    body = _request_body(workload)
    headers = {
        "Accept": "application/json",
        "X-Benchmark-Run-Id": run_id,
        "X-Benchmark-Request-Index": str(request_index),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
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
        if not success:
            error = f"HTTP {status_code}"
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

    if workload.warmup:
        await asyncio.gather(*(invoke(-index) for index in range(1, workload.warmup + 1)))
    return list(
        await asyncio.gather(*(invoke(index) for index in range(1, workload.iterations + 1)))
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


async def run_benchmark(config: RunConfig, workload: Workload) -> dict[str, Any]:
    validate_run_id(config.run_id)
    plan = build_plan(config, workload)
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
    if config.sign_requests:
        credentials = frozen_credentials(boto3.Session(region_name=config.region))
    records: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=config.timeout_seconds, transport=config.transport
    ) as client:
        for implementation in selected_targets(config.target):
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
