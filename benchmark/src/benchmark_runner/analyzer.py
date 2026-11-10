"""保存済み測定値を実装別・起動種別に集計し、比較用の統計値へ変換する。"""

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
    """測定値を昇順に並べ、隣接値の線形補間で指定百分位点を求める。"""

    # 測定値が一件もない場合は、ゼロと誤認させず未計測を表すNoneを返す。
    if not values:
        return None

    # 百分位は0以上1以下だけを許可し、範囲外なら集計設定の誤りとして拒否する。
    if not 0 <= quantile <= 1:
        raise ValueError("quantile は0以上1以下で指定してください")

    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    # 算出位置が整数なら該当値をそのまま返し、小数なら上下の測定値を比率で補間する。
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_summary(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """真偽値を除く数値測定だけを抽出し、件数と主要百分位点を返す。"""
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


def is_successful(record: dict[str, Any]) -> bool:
    """成功した要求かを返す。真偽値のTrueだけを成功とし、欠損や文字列は失敗とする。"""
    return record.get("success") is True


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """測定レコードを実装別、さらにCold/Warm別に集計する。

    時間・メモリの統計は成功した要求だけから求める。失敗した要求の速さを成功の速さとして
    比べないためで、失敗は件数・率と別の統計（failed_metrics）で示す。
    Cold/Warm/未分類の件数も、統計と同じ成功した要求について数える。
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record["implementation"])].append(record)

    implementations: dict[str, Any] = {}
    for implementation, samples in sorted(groups.items()):
        succeeded = [record for record in samples if is_successful(record)]
        failed = [record for record in samples if not is_successful(record)]
        # start_type が cold/warm と明示された成功を分け、分類不能な件数も失わず集計する。
        cold = [record for record in succeeded if record.get("start_type") == "cold"]
        warm = [record for record in succeeded if record.get("start_type") == "warm"]
        implementations[implementation] = {
            "sample_count": len(samples),
            "success_count": len(succeeded),
            "error_count": len(failed),
            "error_rate": len(failed) / len(samples) if samples else None,
            # 本文まで確かめた成功かどうか。確認前の版で計測した結果は、成功の内容が分からない。
            "response_checked": bool(samples)
            and all(record.get("response_checked") is True for record in samples),
            "cold_count": len(cold),
            "warm_count": len(warm),
            "unclassified_count": len(succeeded) - len(cold) - len(warm),
            "metrics": {field: metric_summary(succeeded, field) for field in METRICS},
            "cold_metrics": {field: metric_summary(cold, field) for field in METRICS},
            "warm_metrics": {field: metric_summary(warm, field) for field in METRICS},
            "failed_metrics": {field: metric_summary(failed, field) for field in METRICS},
        }
    return {"implementations": implementations}


def read_records(run_dir: Path) -> list[dict[str, Any]]:
    """CloudWatch結合済み結果を優先し、なければAPI計測結果だけを読み込む。"""
    path = run_dir / "merged.jsonl"
    # merged.jsonl が存在しない場合は、ログ収集前でも分析できるようsamples.jsonlへ切り替える。
    if not path.is_file():
        path = run_dir / "samples.jsonl"
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            # 空行はレコードとして数えず、手動編集で入った改行を許容する。
            if not line.strip():
                continue
            value = json.loads(line)
            # 各JSONL行がオブジェクトでない場合は、集計前提を満たさない行番号を示して停止する。
            if not isinstance(value, dict):
                raise ValueError(
                    f"レコードはオブジェクトである必要があります: {path}:{line_number}"
                )
            records.append(value)
    return records
