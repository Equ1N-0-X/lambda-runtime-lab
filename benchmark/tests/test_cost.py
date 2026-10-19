"""確認済み料金の読込条件と、一要求・百万要求換算の料金計算を検証する。"""

from decimal import Decimal
from pathlib import Path

import pytest

from benchmark_runner.cost import (
    PricingUnavailable,
    calculate_cost,
    load_pricing,
    summarize_costs,
)

ROOT = Path(__file__).resolve().parents[2]


def test_repository_pricing_configures_tier_one_rates():
    pricing = load_pricing(ROOT / "spec" / "pricing.yaml")

    assert pricing.request_price_per_million == Decimal("0.20")
    assert pricing.compute_price_per_gb_second["arm64"] == Decimal("0.0000133334")
    assert pricing.compute_price_per_gb_second["x86_64"] == Decimal("0.0000166667")


def test_calculates_configured_cost_without_free_tier(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        """
updated_at: 2026-09-01
region: ap-northeast-1
source: https://aws.amazon.com/lambda/pricing/
request_price_per_million: 0.2
compute_price_per_gb_second:
  arm64: 0.000016
""",
        encoding="utf-8",
    )
    pricing = load_pricing(path)

    result = calculate_cost(
        billed_duration_ms=100,
        memory_size_mb=512,
        architecture="arm64",
        pricing=pricing,
    )

    assert result["compute_cost"] == pytest.approx(0.0000008)
    assert result["request_cost"] == pytest.approx(0.0000002)
    assert result["normalized_cost_per_1m"] == pytest.approx(1.0)


@pytest.mark.parametrize("value", ["null", "0", "TODO"])
def test_rejects_unconfigured_or_placeholder_prices(tmp_path, value):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        f"""
updated_at: 2026-09-01
region: ap-northeast-1
source: official
request_price_per_million: {value}
compute_price_per_gb_second:
  arm64: 0.000016
""",
        encoding="utf-8",
    )

    with pytest.raises(PricingUnavailable):
        load_pricing(path)


def test_summarizes_compute_cost_without_request_charge():
    pricing = load_pricing(ROOT / "spec" / "pricing.yaml")
    records = [
        {"implementation": "rust", "billed_duration_ms": 2, "memory_size_mb": 512},
        {"implementation": "rust", "billed_duration_ms": 4, "memory_size_mb": 512},
    ]

    summary = summarize_costs(
        records, architecture="arm64", default_memory_mb=512, pricing=pricing
    )["rust"]

    # 平均3msを0.5GBで実行した料金を百万回分にした値。リクエスト料金$0.20を含まない。
    expected_compute = 0.003 * 0.5 * 0.0000133334 * 1_000_000
    assert summary["estimated_compute_cost_per_1m"] == pytest.approx(expected_compute)
    assert summary["estimated_cost_per_1m"] == pytest.approx(expected_compute + 0.20)
