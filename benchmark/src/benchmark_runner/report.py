from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from benchmark_runner.analyzer import METRICS, read_records, summarize
from benchmark_runner.cost import PricingUnavailable, load_pricing, summarize_costs


def _display(value: object, digits: int = 6) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _difference(reference: object, comparison: object) -> float | None:
    if not isinstance(reference, int | float) or isinstance(reference, bool):
        return None
    if not isinstance(comparison, int | float) or isinstance(comparison, bool):
        return None
    if reference == 0:
        return None
    return (float(comparison) - float(reference)) / float(reference) * 100


def _load_optional_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def generate_report(
    *,
    run_id: str,
    results_dir: Path,
    pricing_path: Path,
) -> tuple[Path, Path]:
    run_dir = results_dir / "raw" / run_id
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    records = read_records(run_dir)
    analysis = summarize(records)
    image_metrics = _load_optional_json(run_dir / "image-metrics.json")

    pricing_note: str | None = None
    pricing_metadata: dict[str, Any] | None = None
    try:
        pricing = load_pricing(pricing_path)
        costs = summarize_costs(
            records,
            architecture=metadata.get("architecture"),
            default_memory_mb=metadata.get("memory_mb"),
            pricing=pricing,
        )
        pricing_metadata = {
            "updated_at": pricing.updated_at,
            "region": pricing.region,
            "source": pricing.source,
        }
    except PricingUnavailable as exc:
        costs = {}
        pricing_note = str(exc)

    implementations = analysis["implementations"]
    for name, values in implementations.items():
        values["cost"] = costs.get(name)

    reports_dir = results_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = reports_dir / f"{run_id}.md"
    csv_path = reports_dir / f"{run_id}.csv"

    lines = [
        f"# Benchmark report: {run_id}",
        "",
        "## Experiment conditions",
        "",
        f"- Run ID: `{run_id}`",
        f"- Started: {_display(metadata.get('started_at'))}",
        f"- Completed: {_display(metadata.get('completed_at'))}",
        f"- Git revision: {_display(metadata.get('git_revision'))}",
        f"- AWS region: {_display(metadata.get('region'))}",
        f"- Architecture: {_display(metadata.get('architecture'))}",
        f"- Lambda memory: {_display(metadata.get('memory_mb'))} MB",
        f"- Workload: {_display(metadata.get('workload', {}).get('name'))}",
        f"- Iterations: {_display(metadata.get('workload', {}).get('iterations'))}",
        f"- Client concurrency: {_display(metadata.get('workload', {}).get('client_concurrency'))}",
        f"- Warm-up: {_display(metadata.get('workload', {}).get('warmup'))}",
        "",
        "## Summary",
        "",
        "| Implementation | Samples | Cold | Warm | Unclassified | Error rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in implementations.items():
        error_rate = values["error_rate"]
        rendered_rate = "null" if error_rate is None else f"{error_rate:.2%}"
        lines.append(
            f"| {name} | {values['sample_count']} | {values['cold_count']} | "
            f"{values['warm_count']} | {values['unclassified_count']} | "
            f"{rendered_rate} |"
        )

    lines.extend(
        [
            "",
            "## Percentiles",
            "",
            "| Implementation | Scope | Metric | Count | P50 | P95 | P99 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in implementations.items():
        for scope, key in (
            ("all", "metrics"),
            ("cold", "cold_metrics"),
            ("warm", "warm_metrics"),
        ):
            for metric in METRICS:
                stats = values[key][metric]
                lines.append(
                    f"| {name} | {scope} | {metric} | {stats['count']} | "
                    f"{_display(stats['p50'])} | {_display(stats['p95'])} | "
                    f"{_display(stats['p99'])} |"
                )

    lines.extend(["", "## Estimated cost (Free Tier excluded)", ""])
    if pricing_note:
        lines.append(f"Cost: null — {pricing_note}")
    else:
        lines.extend(
            [
                f"- Pricing updated: {_display(pricing_metadata['updated_at'])}",
                f"- Pricing region: {_display(pricing_metadata['region'])}",
                f"- Pricing source: {_display(pricing_metadata['source'])}",
                "",
                "| Implementation | Priced samples | Cost/request | Cost/1M requests |",
                "|---|---:|---:|---:|",
            ]
        )
        for name, values in implementations.items():
            cost = values["cost"]
            if cost is None:
                lines.append(f"| {name} | 0 | null | null |")
            else:
                lines.append(
                    f"| {name} | {cost['priced_sample_count']} | "
                    f"{_display(cost['estimated_cost_per_request'], 10)} | "
                    f"{_display(cost['estimated_cost_per_1m'], 10)} |"
                )

    lines.extend(["", "## Python/Rust difference", ""])
    if {"python", "rust"} <= implementations.keys():
        lines.extend(
            [
                "Positive values mean Rust is higher than Python.",
                "",
                "| Metric | Percentile | Difference |",
                "|---|---|---:|",
            ]
        )
        for metric in METRICS:
            for percentile_name in ("p50", "p95", "p99"):
                difference = _difference(
                    implementations["python"]["metrics"][metric][percentile_name],
                    implementations["rust"]["metrics"][metric][percentile_name],
                )
                lines.append(
                    f"| {metric} | {percentile_name.upper()} | "
                    f"{'null' if difference is None else f'{difference:.2f}%'} |"
                )
    else:
        lines.append("Comparison: null — both implementations are required.")

    lines.extend(["", "## Image metrics", ""])
    if image_metrics is None:
        lines.append("Image metrics: null — image-metrics.json was not collected.")
    else:
        lines.append("```json")
        lines.append(json.dumps(image_metrics, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")

    lines.extend(
        [
            "",
            "## Measurement notes",
            "",
            "- Missing measurements are shown as `null`; they are never replaced with zero.",
            "- A sample is cold only when Lambda REPORT contains Init Duration.",
            "- Samples with a REPORT but no Init Duration are warm; "
            "unmatched samples are unclassified.",
            "- Cost is normalized without subtracting the AWS Free Tier.",
            "- Image size is an artifact metric and is not part of Lambda execution pricing.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    fieldnames = [
        "run_id",
        "implementation",
        "sample_count",
        "cold_count",
        "warm_count",
        "unclassified_count",
        "error_count",
        "error_rate",
    ]
    for metric in METRICS:
        fieldnames.extend(f"{metric}_{percentile}" for percentile in ("p50", "p95", "p99"))
    fieldnames.extend(
        ["priced_sample_count", "estimated_cost_per_request", "estimated_cost_per_1m"]
    )
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for name, values in implementations.items():
            row: dict[str, Any] = {
                "run_id": run_id,
                "implementation": name,
                "sample_count": values["sample_count"],
                "cold_count": values["cold_count"],
                "warm_count": values["warm_count"],
                "unclassified_count": values["unclassified_count"],
                "error_count": values["error_count"],
                "error_rate": values["error_rate"],
            }
            for metric in METRICS:
                for percentile_name in ("p50", "p95", "p99"):
                    row[f"{metric}_{percentile_name}"] = values["metrics"][metric][percentile_name]
            cost = values["cost"] or {}
            row.update(
                {
                    "priced_sample_count": cost.get("priced_sample_count"),
                    "estimated_cost_per_request": cost.get("estimated_cost_per_request"),
                    "estimated_cost_per_1m": cost.get("estimated_cost_per_1m"),
                }
            )
            writer.writerow(row)
    return markdown_path, csv_path
