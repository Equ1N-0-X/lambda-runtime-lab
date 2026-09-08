from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_PATTERN = re.compile(
    r"REPORT RequestId:\s*(?P<request_id>\S+)"
    r"\s+Duration:\s*(?P<duration>[\d.]+)\s*ms"
    r"\s+Billed Duration:\s*(?P<billed>[\d.]+)\s*ms"
    r"\s+Memory Size:\s*(?P<memory>\d+)\s*MB"
    r"\s+Max Memory Used:\s*(?P<max_memory>\d+)\s*MB"
    r"(?:\s+Init Duration:\s*(?P<init>[\d.]+)\s*ms)?"
)


def parse_report(message: str) -> dict[str, Any] | None:
    match = REPORT_PATTERN.search(message)
    if not match:
        return None
    values = match.groupdict()
    return {
        "lambda_request_id": values["request_id"],
        "duration_ms": float(values["duration"]),
        "billed_duration_ms": float(values["billed"]),
        "memory_size_mb": int(values["memory"]),
        "max_memory_used_mb": int(values["max_memory"]),
        "init_duration_ms": float(values["init"]) if values["init"] else None,
    }


def parse_structured_log(message: str) -> dict[str, Any] | None:
    opening = message.find("{")
    if opening < 0:
        return None
    try:
        value = json.loads(message[opening:])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    request_id = (
        value.get("lambda_request_id") or value.get("request_id") or value.get("aws_request_id")
    )
    run_id = value.get("benchmark_run_id") or value.get("run_id")
    if not isinstance(request_id, str) or not isinstance(run_id, str):
        return None
    return {
        "lambda_request_id": request_id,
        "benchmark_run_id": run_id,
        "implementation": value.get("implementation"),
        "route": value.get("route"),
        "profile": value.get("profile"),
        "status_code": value.get("status_code"),
    }


def correlate_samples(
    samples: list[dict[str, Any]],
    messages: list[str],
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reports: dict[str, dict[str, Any]] = {}
    structured: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for message in messages:
        report = parse_report(message)
        if report:
            reports[report["lambda_request_id"]] = report
            events.append({"type": "report", **report})
            continue
        app_log = parse_structured_log(message)
        if app_log and app_log["benchmark_run_id"] == run_id:
            structured[app_log["lambda_request_id"]] = app_log
            events.append({"type": "structured", **app_log})

    merged: list[dict[str, Any]] = []
    for sample in samples:
        request_id = sample.get("lambda_request_id")
        app_log = structured.get(request_id, {}) if request_id else {}
        report = reports.get(request_id, {}) if request_id else {}
        merged.append(
            {
                **sample,
                "lambda_request_id": request_id,
                "benchmark_run_id": app_log.get("benchmark_run_id"),
                "duration_ms": report.get("duration_ms"),
                "billed_duration_ms": report.get("billed_duration_ms"),
                "init_duration_ms": report.get("init_duration_ms"),
                "memory_size_mb": report.get("memory_size_mb"),
                "max_memory_used_mb": report.get("max_memory_used_mb"),
                "start_type": (
                    "cold"
                    if report.get("init_duration_ms") is not None
                    else "warm"
                    if report
                    else None
                ),
                "log_matched": bool(app_log),
                "report_matched": bool(report),
            }
        )
    return merged, events


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record must be an object at {path}:{line_number}")
            records.append(value)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _epoch(iso_value: str, offset_seconds: int) -> int:
    value = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp()) + offset_seconds


def _insights_messages(
    logs_client: object,
    log_group: str,
    start_time: int,
    end_time: int,
) -> list[str]:
    response = logs_client.start_query(  # type: ignore[attr-defined]
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString="fields @timestamp, @message | sort @timestamp asc",
        limit=10_000,
    )
    query_id = response["queryId"]
    for _ in range(120):
        result = logs_client.get_query_results(queryId=query_id)  # type: ignore[attr-defined]
        status = result["status"]
        if status == "Complete":
            return [
                next(
                    (field["value"] for field in row if field["field"] == "@message"),
                    "",
                )
                for row in result.get("results", [])
            ]
        if status in {"Cancelled", "Failed", "Timeout", "Unknown"}:
            raise RuntimeError(f"CloudWatch Logs Insights query ended with status {status}")
        time.sleep(0.5)
    raise TimeoutError("CloudWatch Logs Insights query did not complete")


def _filter_messages(
    logs_client: object,
    log_group: str,
    start_time: int,
    end_time: int,
) -> list[str]:
    messages: list[str] = []
    token: str | None = None
    while True:
        arguments: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_time * 1000,
            "endTime": end_time * 1000,
        }
        if token:
            arguments["nextToken"] = token
        response = logs_client.filter_log_events(**arguments)  # type: ignore[attr-defined]
        messages.extend(event.get("message", "") for event in response.get("events", []))
        next_token = response.get("nextToken")
        if not next_token or next_token == token:
            return messages
        token = next_token


def collect_run(
    *,
    run_id: str,
    results_dir: Path,
    log_groups: dict[str, str],
    logs_client: object,
    strategy: str = "auto",
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir = results_dir / "raw" / run_id
    metadata_path = run_dir / "metadata.json"
    samples_path = run_dir / "samples.jsonl"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    start_time = _epoch(metadata["started_at"], -300)
    end_time = _epoch(metadata["completed_at"], 300)
    plan = {
        "run_id": run_id,
        "log_groups": log_groups,
        "strategy": strategy,
        "start_time": start_time,
        "end_time": end_time,
        "dry_run": dry_run,
    }
    if dry_run:
        return plan

    samples = _read_jsonl(samples_path)
    all_events: list[dict[str, Any]] = []
    all_merged: list[dict[str, Any]] = []
    for implementation in sorted(log_groups):
        log_group = log_groups[implementation]
        try:
            if strategy == "filter":
                raise RuntimeError("filter strategy selected")
            messages = _insights_messages(logs_client, log_group, start_time, end_time)
            source = "insights"
        except Exception:
            if strategy == "insights":
                raise
            messages = _filter_messages(logs_client, log_group, start_time, end_time)
            source = "filter"
        selected = [row for row in samples if row["implementation"] == implementation]
        merged, events = correlate_samples(selected, messages, run_id=run_id)
        all_merged.extend(merged)
        all_events.extend(
            {"implementation": implementation, "source": source, **event} for event in events
        )

    implementations = set(log_groups)
    all_merged.extend(
        {
            **sample,
            "duration_ms": None,
            "billed_duration_ms": None,
            "init_duration_ms": None,
            "memory_size_mb": None,
            "max_memory_used_mb": None,
            "start_type": None,
            "log_matched": False,
            "report_matched": False,
        }
        for sample in samples
        if sample["implementation"] not in implementations
    )
    all_merged.sort(key=lambda row: (row["implementation"], row["request_index"]))
    _write_jsonl(run_dir / "cloudwatch.jsonl", all_events)
    _write_jsonl(run_dir / "merged.jsonl", all_merged)
    return {**plan, "sample_count": len(all_merged), "event_count": len(all_events)}
