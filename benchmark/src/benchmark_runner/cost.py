"""確認済み料金表と測定値から、実装別の推定Lambda実行コストを計算する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


class PricingUnavailable(ValueError):
    """料金が未確認、プレースホルダー、または計算不能な場合に通知する。"""


@dataclass(frozen=True)
class Pricing:
    updated_at: str
    region: str
    source: str
    request_price_per_million: Decimal
    compute_price_per_gb_second: dict[str, Decimal]


def _positive_decimal(value: object, field: str) -> Decimal:
    """料金をDecimalへ変換し、有限の正数であることを確認する。"""
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PricingUnavailable(f"{field} が設定されていません") from exc

    # 無限・NaN・ゼロ以下は実料金として使えないため、設定済みとは扱わない。
    if not decimal.is_finite() or decimal <= 0:
        raise PricingUnavailable(f"{field} には確認済みの正の料金を設定してください")
    return decimal


def load_pricing(path: Path) -> Pricing:
    """料金YAMLを読み、出典情報と少なくとも一つのアーキテクチャ料金を検証する。"""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PricingUnavailable(f"料金ファイルが見つかりません: {path}") from exc
    except yaml.YAMLError as exc:
        raise PricingUnavailable(f"料金YAMLが不正です: {exc}") from exc

    # ルートがマッピングでない場合は料金項目を参照できないため拒否する。
    if not isinstance(document, dict):
        raise PricingUnavailable("料金定義はマッピング形式で指定してください")

    required_text = {}
    for field in ("updated_at", "region", "source"):
        value = document.get(field)
        # 確認日・リージョン・出典が空またはTODOなら、未確認料金による推定を防ぐため拒否する。
        if value is None or not str(value).strip() or "TODO" in str(value).upper():
            raise PricingUnavailable(f"{field} が設定されていません")
        required_text[field] = str(value)

    raw_compute = document.get("compute_price_per_gb_second")
    # アーキテクチャ別料金がマッピングでない場合は、計算対象を選べないため拒否する。
    if not isinstance(raw_compute, dict):
        raise PricingUnavailable("compute_price_per_gb_second が設定されていません")
    compute = {
        architecture: _positive_decimal(raw_compute.get(architecture), f"price.{architecture}")
        for architecture in ("arm64", "x86_64")
        if raw_compute.get(architecture) is not None
    }
    # 有効なアーキテクチャ料金が一件もなければ、推定値を作らず未設定として停止する。
    if not compute:
        raise PricingUnavailable("アーキテクチャ別の計算料金が設定されていません")
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
    """一リクエスト分の実行時間料金・リクエスト料金と、百万回換算値を返す。"""

    # 測定アーキテクチャの料金がない場合は、別方式の料金を代用せず計算を停止する。
    if architecture not in pricing.compute_price_per_gb_second:
        raise PricingUnavailable(f"アーキテクチャの料金が設定されていません: {architecture}")

    # 実行時間はゼロ以上、割当メモリは正数の場合だけ物理的に意味のある料金を計算する。
    if billed_duration_ms < 0 or memory_size_mb <= 0:
        raise ValueError("実行時間は0以上、メモリは正数である必要があります")
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
    """料金計算に必要な測定だけを実装別に集め、平均推定コストを返す。"""

    # 実行メタデータにアーキテクチャがなければ料金表を選べないため、推定しない。
    if not architecture:
        raise PricingUnavailable("実行メタデータにアーキテクチャがありません")
    grouped: dict[str, list[dict[str, float]]] = {}
    for record in records:
        billed = record.get("billed_duration_ms")
        memory = record.get("memory_size_mb") or default_memory_mb
        # 課金時間が数値でない測定は料金計算不能なため、件数へ含めず読み飛ばす。
        if not isinstance(billed, int | float) or isinstance(billed, bool):
            continue
        # メモリ量が整数でない測定も料金計算不能なため、件数へ含めず読み飛ばす。
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
