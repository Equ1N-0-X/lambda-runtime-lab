from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

METRICS = (
    "latency_ms",
    "duration_ms",
    "billed_duration_ms",
    "init_duration_ms",
    "max_memory_used_mb",
)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_summary(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        float(record[field])
        for record in records
        if isinstance(record.get(field), int | float) and not isinstance(record.get(field), bool)
    ]
    return {
        "count": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record["implementation"])].append(record)

    implementations: dict[str, Any] = {}
    for implementation, samples in sorted(groups.items()):
        cold = [record for record in samples if record.get("start_type") == "cold"]
        warm = [record for record in samples if record.get("start_type") == "warm"]
        error_count = sum(not bool(record.get("success")) for record in samples)
        implementations[implementation] = {
            "sample_count": len(samples),
            "error_count": error_count,
            "error_rate": error_count / len(samples) if samples else None,
            "cold_count": len(cold),
            "warm_count": len(warm),
            "unclassified_count": len(samples) - len(cold) - len(warm),
            "metrics": {field: metric_summary(samples, field) for field in METRICS},
            "cold_metrics": {field: metric_summary(cold, field) for field in METRICS},
            "warm_metrics": {field: metric_summary(warm, field) for field in METRICS},
        }
    return {"implementations": implementations}


def read_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "merged.jsonl"
    if not path.is_file():
        path = run_dir / "samples.jsonl"
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"record must be an object at {path}:{line_number}")
            records.append(value)
    return records
