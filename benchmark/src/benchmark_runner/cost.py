from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


class PricingUnavailable(ValueError):
    """Raised when pricing is missing, placeholder, or invalid."""


@dataclass(frozen=True)
class Pricing:
    updated_at: str
    region: str
    source: str
    request_price_per_million: Decimal
    compute_price_per_gb_second: dict[str, Decimal]


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PricingUnavailable(f"{field} is not configured") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise PricingUnavailable(f"{field} must be a positive configured price")
    return decimal


def load_pricing(path: Path) -> Pricing:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PricingUnavailable(f"pricing file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PricingUnavailable(f"invalid pricing YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise PricingUnavailable("pricing document must be a mapping")

    required_text = {}
    for field in ("updated_at", "region", "source"):
        value = document.get(field)
        if value is None or not str(value).strip() or "TODO" in str(value).upper():
            raise PricingUnavailable(f"{field} is not configured")
        required_text[field] = str(value)

    raw_compute = document.get("compute_price_per_gb_second")
    if not isinstance(raw_compute, dict):
        raise PricingUnavailable("compute_price_per_gb_second is not configured")
    compute = {
        architecture: _positive_decimal(raw_compute.get(architecture), f"price.{architecture}")
        for architecture in ("arm64", "x86_64")
        if raw_compute.get(architecture) is not None
    }
    if not compute:
        raise PricingUnavailable("no architecture compute price is configured")
    return Pricing(
        updated_at=required_text["updated_at"],
        region=required_text["region"],
        source=required_text["source"],
        request_price_per_million=_positive_decimal(
            document.get("request_price_per_million"), "request_price_per_million"
        ),
        compute_price_per_gb_second=compute,
    )


def calculate_cost(
    *,
    billed_duration_ms: float,
    memory_size_mb: int,
    architecture: str,
    pricing: Pricing,
) -> dict[str, float]:
    if architecture not in pricing.compute_price_per_gb_second:
        raise PricingUnavailable(f"price is not configured for architecture: {architecture}")
    if billed_duration_ms < 0 or memory_size_mb <= 0:
        raise ValueError("duration must be non-negative and memory must be positive")
    duration_seconds = Decimal(str(billed_duration_ms)) / Decimal(1000)
    memory_gb = Decimal(memory_size_mb) / Decimal(1024)
    compute = duration_seconds * memory_gb * pricing.compute_price_per_gb_second[architecture]
    request = pricing.request_price_per_million / Decimal(1_000_000)
    total = compute + request
    return {
        "compute_cost": float(compute),
        "request_cost": float(request),
        "total_cost": float(total),
        "normalized_cost_per_1m": float(total * Decimal(1_000_000)),
    }


def summarize_costs(
    records: list[dict[str, Any]],
    *,
    architecture: str | None,
    default_memory_mb: int | None,
    pricing: Pricing,
) -> dict[str, Any]:
    if not architecture:
        raise PricingUnavailable("architecture is unavailable in run metadata")
    grouped: dict[str, list[dict[str, float]]] = {}
    for record in records:
        billed = record.get("billed_duration_ms")
        memory = record.get("memory_size_mb") or default_memory_mb
        if not isinstance(billed, int | float) or isinstance(billed, bool):
            continue
        if not isinstance(memory, int) or isinstance(memory, bool):
            continue
        implementation = str(record["implementation"])
        grouped.setdefault(implementation, []).append(
            calculate_cost(
                billed_duration_ms=float(billed),
                memory_size_mb=memory,
                architecture=architecture,
                pricing=pricing,
            )
        )
    result: dict[str, Any] = {}
    for implementation, costs in grouped.items():
        result[implementation] = {
            "priced_sample_count": len(costs),
            "estimated_cost_per_request": sum(item["total_cost"] for item in costs) / len(costs),
            "estimated_cost_per_1m": sum(item["normalized_cost_per_1m"] for item in costs)
            / len(costs),
        }
    return result
