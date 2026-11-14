"""確認済み料金表と測定値から、実装別の推定Lambda実行コストを計算する。"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


class PricingUnavailable(ValueError):
    """料金が未確認、プレースホルダー、または計算不能な場合に通知する。"""


@dataclass(frozen=True)
class OtherServicePricing:
    """計測1回の概算に使う、Lambda以外のサービスの単価。"""

    api_gateway_http_request_price_per_million: Decimal
    dynamodb_read_request_unit_price_per_million: Decimal
    cloudwatch_logs_ingestion_price_per_gb: Decimal


@dataclass(frozen=True)
class Pricing:
    updated_at: str
    region: str
    source: str
    request_price_per_million: Decimal
    compute_price_per_gb_second: dict[str, Decimal]
    # 実行時間単価を適用できる月間使用量（GB秒）の上限。未設定の方式は上限を確かめられない。
    tier1_max_gb_seconds_per_month: dict[str, Decimal] = dataclass_field(default_factory=dict)
    # Lambda以外のサービスの単価。未設定の料金表では、それらの料金を概算しない。
    other_services: OtherServicePricing | None = None


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
    raw_other = document.get("other_services")
    other_services = None
    # Lambda以外の単価は任意の項目とし、書かれている場合だけ全項目を確認済みの正の料金として読む。
    if raw_other is not None:
        if not isinstance(raw_other, dict):
            raise PricingUnavailable("other_services はマッピング形式で指定してください")
        other_services = OtherServicePricing(
            **{
                name: _positive_decimal(raw_other.get(name), f"other_services.{name}")
                for name in OtherServicePricing.__dataclass_fields__
            }
        )
    return Pricing(
        updated_at=required_text["updated_at"],
        region=required_text["region"],
        source=required_text["source"],
        request_price_per_million=_positive_decimal(
            document.get("request_price_per_million"), "request_price_per_million"
        ),
        compute_price_per_gb_second=compute,
        tier1_max_gb_seconds_per_month={
            architecture: _positive_decimal(value, f"tier1_max_gb_seconds_per_month.{architecture}")
            for architecture, value in (
                document.get("tier1_max_gb_seconds_per_month") or {}
            ).items()
        },
        other_services=other_services,
    )


def check_pricing_region(pricing: Pricing, region: object) -> None:
    """計測した地域に料金表を適用できるか確かめ、できなければ理由を通知する。

    Lambdaの単価は地域ごとに異なるため、別地域の料金表で計算した金額は正しくない。
    """
    # 計測の地域が記録されていなければ、どの地域の料金を使うべきか決められない。
    if not isinstance(region, str) or not region:
        raise PricingUnavailable("計測した地域が記録されていないため、料金表を適用できません")
    # 料金表の地域と計測の地域が異なれば、別地域の単価で計算することになるため止める。
    if region != pricing.region:
        raise PricingUnavailable(
            f"料金表の地域（{pricing.region}）と計測した地域（{region}）が異なります"
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
    """料金計算に必要な測定だけを実装別に集め、平均推定コストを返す。

    リクエスト料金は実装によらず同額のため、実装差を見られるよう実行時間分だけの料金も返す。
    """

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
            # 計測した要求の料金の合計。失敗した要求に課金された額を示すために使う。
            "estimated_total_cost": sum(item["total_cost"] for item in costs),
            "estimated_cost_per_1m": sum(item["normalized_cost_per_1m"] for item in costs)
            / len(costs),
            "estimated_compute_cost_per_1m": sum(item["compute_cost"] for item in costs)
            / len(costs)
            * 1_000_000,
        }
    return result


# CloudWatch Logs が取り込み量として課金する、ログ1行あたりの付加情報の大きさ（バイト）。
LOG_EVENT_OVERHEAD_BYTES = 26
BYTES_PER_GB = Decimal(1024**3)


def estimate_run_cost(
    *,
    requests_sent: dict[str, int],
    lambda_cost_per_request: dict[str, float | None],
    dynamodb_reads_per_request: int | None,
    log_bytes: int | None,
    pricing: Pricing,
) -> dict[str, Any]:
    """計測1回で送った全リクエスト（ウォームアップを含む）にかかった料金の概算を、内訳付きで返す。

    100万リクエストあたりの料金とは別に、その計測そのものの費用を示すために使う。
    根拠がそろわない項目は0ではなくNoneとし、合計には算出できた項目だけを含める。
    """
    total_requests = sum(requests_sent.values())
    million = Decimal(1_000_000)

    lambda_cost: Decimal | None = Decimal(0)
    for implementation, count in requests_sent.items():
        per_request = lambda_cost_per_request.get(implementation)
        # 料金を計算できなかった言語が一つでもあれば、一部だけの合計で過小に見せないよう算出しない。
        if per_request is None:
            lambda_cost = None
            break
        lambda_cost += Decimal(str(per_request)) * count

    other = pricing.other_services
    api_gateway = dynamodb = cloudwatch_logs = None
    read_units = None
    # Lambda以外の単価が料金表にある場合だけ、それらのサービスの料金を概算する。
    if other is not None:
        api_gateway = other.api_gateway_http_request_price_per_million * total_requests / million
        # 1リクエストの読み取り件数が記録されている計測だけ、DynamoDBの料金を求める。
        # 強整合の読み取りで4KB以下の項目を読むため、1件を1読み取り要求ユニットとして数える。
        if dynamodb_reads_per_request is not None:
            read_units = dynamodb_reads_per_request * total_requests
            dynamodb = other.dynamodb_read_request_unit_price_per_million * read_units / million
        # 収集したログの量が記録されている計測だけ、CloudWatch Logsの取り込み料金を求める。
        if log_bytes is not None:
            cloudwatch_logs = (
                other.cloudwatch_logs_ingestion_price_per_gb * log_bytes / BYTES_PER_GB
            )

    components = {
        "lambda": lambda_cost,
        "api_gateway": api_gateway,
        "dynamodb": dynamodb,
        "cloudwatch_logs": cloudwatch_logs,
    }
    known = [value for value in components.values() if value is not None]
    return {
        "requests": total_requests,
        "components": {
            name: None if value is None else float(value) for name, value in components.items()
        },
        "dynamodb_read_units": read_units,
        "log_bytes": log_bytes,
        "total": float(sum(known, Decimal(0))),
        # すべての項目を算出できたか。できていなければ合計は一部の項目だけの金額である。
        "complete": len(known) == len(components),
    }
