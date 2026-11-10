"""作りたいAPIの処理の構成と月間リクエスト数から、言語ごとの料金と処理時間を見込む。

利用者のAPIを、計測済みのワークロードの組み合わせ（構成割合）として表し、
各ワークロードの実測を割合で重み付けして合算する。料金の計算式は計測結果の料金
（cost.calculate_cost）と同じものを使い、画面の他の料金表示と整合させる。

見込みの式（言語ごと）:
    1リクエストあたり料金 = Σ 割合 × {(1 - コールド率) × ウォーム時の平均料金
                                      + コールド率 × コールド時の平均料金}
    月額料金 = 1リクエストあたり料金 × 月間リクエスト数
コールド時の実測がないワークロードは、ウォーム時の料金に、その言語の初期化時間
（中央値）の分の実行時間料金を足して近似する（Lambdaは初期化時間も課金対象のため）。
その言語に初期化時間の実測が一つもなければ近似できないため、コールドスタート分を含めず、
含めていないことを結果に示す（見込みは実際より低くなる）。

根拠にする計測は、比較可能で、選んだ条件（アーキテクチャ・メモリ）と料金表の地域が同じ計測に限る。
ワークロードごとに最新の計測と同じ処理内容（実験の識別子）の計測だけを使い、
言語ごとに、構成に含む全ワークロードの根拠が同じイメージ（digest）であることを求める。
料金表の単価を適用できる月間使用量（Tier 1）を超える見込みは参考値として示す。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from benchmark_runner.analyzer import is_successful, read_records
from benchmark_runner.cost import Pricing, PricingUnavailable, calculate_cost, load_pricing
from benchmark_runner.dashboard.catalog import list_runs
from benchmark_runner.implementations import DEFAULT_REGISTRY, load_implementations

# 誤入力で極端な値を計算しないよう、受け付ける範囲を制限する。
MAX_MONTHLY_REQUESTS = 100_000_000_000
MAX_COLD_START_PERCENT = 100


class EstimateError(ValueError):
    """見込みを計算できない入力であることを、利用者が直せる文言で通知する。"""


@dataclass(frozen=True)
class WorkloadBasis:
    """一つのワークロード・言語について、見込みの根拠にする実測値。"""

    run_id: str
    started_at: str | None
    warm_cost: float
    cold_cost: float | None
    warm_duration_ms: float
    init_duration_ms: float | None
    # 月間の使用量（GB秒）を求めるための、課金対象時間の平均。
    warm_billed_ms: float = 0.0
    cold_billed_ms: float | None = None
    image_digest: str | None = None


def _numbers(records: list[dict[str, Any]], field: str) -> list[float]:
    return [
        float(record[field])
        for record in records
        if isinstance(record.get(field), int | float) and not isinstance(record.get(field), bool)
    ]


def _mean_cost(
    records: list[dict[str, Any]], *, memory_mb: int, architecture: str, pricing: Pricing
) -> float | None:
    """課金対象時間がそろう計測について、1リクエストあたりの平均料金を返す。"""
    costs = [
        calculate_cost(
            billed_duration_ms=billed,
            memory_size_mb=memory_mb,
            architecture=architecture,
            pricing=pricing,
        )["total_cost"]
        for billed in _numbers(records, "billed_duration_ms")
    ]
    return statistics.fmean(costs) if costs else None


def _basis_for_run(
    run_dir: Path, language: str, *, memory_mb: int, architecture: str, pricing: Pricing
) -> WorkloadBasis | None:
    """一つの計測から、言語のウォーム・コールド別の料金と時間を求める。"""
    # 失敗した要求の料金・時間は、成功した処理の見込みに混ぜない。
    records = [
        record
        for record in read_records(run_dir)
        if record.get("implementation") == language and is_successful(record)
    ]
    warm = [record for record in records if record.get("start_type") == "warm"]
    cold = [record for record in records if record.get("start_type") == "cold"]
    warm_cost = _mean_cost(warm, memory_mb=memory_mb, architecture=architecture, pricing=pricing)
    warm_durations = _numbers(warm, "duration_ms")
    # ウォームの実測がなければ、普段の料金と処理時間を決められない。
    if warm_cost is None or not warm_durations:
        return None
    init = _numbers(cold, "init_duration_ms")
    cold_billed = _numbers(cold, "billed_duration_ms")
    return WorkloadBasis(
        run_id=run_dir.name,
        started_at=None,
        warm_cost=warm_cost,
        cold_cost=_mean_cost(cold, memory_mb=memory_mb, architecture=architecture, pricing=pricing),
        warm_duration_ms=statistics.fmean(warm_durations),
        init_duration_ms=statistics.median(init) if init else None,
        warm_billed_ms=statistics.fmean(_numbers(warm, "billed_duration_ms")),
        cold_billed_ms=statistics.fmean(cold_billed) if cold_billed else None,
    )


def _eligible_runs(*, results_dir: Path, pricing_path: Path, region: str) -> list[dict[str, Any]]:
    """見込みの根拠に使える計測を新しい順に返す。

    比較可能で、料金表と同じ地域で計測し、処理内容の識別子が記録された計測に限る。
    識別子のない古い計測は、同じ処理内容か確かめられないため使わない。
    """
    return [
        summary
        for summary in list_runs(results_dir=results_dir, pricing_path=pricing_path)
        if summary["comparable"] and summary["region"] == region and summary["experiment_id"]
    ]


def available_conditions(*, results_dir: Path, pricing_path: Path) -> list[dict[str, Any]]:
    """見込みに使える計測条件（アーキテクチャとメモリの組）を、計測の多い順に返す。

    同じ件数なら、アーキテクチャ名・メモリの順に並べて表示順を安定させる。
    """
    try:
        region = load_pricing(pricing_path).region
    # 料金表がなければ見込みを計算できないため、選べる条件もない。
    except PricingUnavailable:
        return []
    seen: dict[tuple[str, int], int] = {}
    for summary in _eligible_runs(
        results_dir=results_dir, pricing_path=pricing_path, region=region
    ):
        key = (summary["architecture"], summary["memory_mb"])
        seen[key] = seen.get(key, 0) + 1
    ordered = sorted(seen.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    return [
        {"architecture": architecture, "memory_mb": memory_mb, "run_count": count}
        for (architecture, memory_mb), count in ordered
    ]


def _validated_mix(mix: object) -> dict[str, float]:
    """構成割合を検証する。0より大きい割合だけを残し、合計は100%でなければならない。"""
    if not isinstance(mix, dict) or not mix:
        raise EstimateError("処理の構成割合を入力してください。")
    shares: dict[str, float] = {}
    for workload, percent in mix.items():
        # 割合は0以上100以下の数値だけを受け付ける。
        if isinstance(percent, bool) or not isinstance(percent, int | float):
            raise EstimateError(f"{workload} の割合は数値で入力してください。")
        if not 0 <= percent <= 100:
            raise EstimateError(f"{workload} の割合は0〜100%で入力してください。")
        if percent > 0:
            shares[str(workload)] = float(percent)
    total = sum(shares.values())
    # 合計が100%からずれていれば、見込みが利用者の意図と違う重みになる。
    if abs(total - 100) > 0.01:
        raise EstimateError(f"割合の合計を100%にしてください（現在 {total:g}%）。")
    return {workload: percent / 100 for workload, percent in shares.items()}


def build_estimate(
    *,
    mix: object,
    monthly_requests: object,
    cold_start_percent: object,
    architecture: object,
    memory_mb: object,
    results_dir: Path,
    pricing_path: Path,
    registry_path: Path = DEFAULT_REGISTRY,
) -> dict[str, Any]:
    """構成割合・月間リクエスト数・コールド率から、言語ごとの見込みと根拠を返す。"""
    shares = _validated_mix(mix)
    # 月間リクエスト数は1以上の整数だけを受け付ける。
    if (
        isinstance(monthly_requests, bool)
        or not isinstance(monthly_requests, int)
        or not 1 <= monthly_requests <= MAX_MONTHLY_REQUESTS
    ):
        raise EstimateError("月間リクエスト数は1以上の整数で入力してください。")
    # コールドスタートの割合は0〜100%の数値だけを受け付ける。
    if (
        isinstance(cold_start_percent, bool)
        or not isinstance(cold_start_percent, int | float)
        or not 0 <= cold_start_percent <= MAX_COLD_START_PERCENT
    ):
        raise EstimateError("コールドスタートの割合は0〜100%で入力してください。")
    if (
        not isinstance(architecture, str)
        or isinstance(memory_mb, bool)
        or not isinstance(memory_mb, int)
    ):
        raise EstimateError("計測条件を選んでください。")
    cold_ratio = float(cold_start_percent) / 100
    pricing = load_pricing(pricing_path)

    # 指定した条件の、根拠に使える計測を新しい順に集める。
    runs = [
        summary
        for summary in _eligible_runs(
            results_dir=results_dir, pricing_path=pricing_path, region=pricing.region
        )
        if summary["architecture"] == architecture
        and summary["memory_mb"] == memory_mb
        and summary["workload"] in shares
    ]
    # ワークロードごとに最新の計測の処理内容を基準にし、本文や同時要求数が違う計測を混ぜない。
    reference: dict[str, str] = {}
    for summary in runs:
        reference.setdefault(summary["workload"], summary["experiment_id"])
    runs = [
        summary for summary in runs if summary["experiment_id"] == reference[summary["workload"]]
    ]

    languages = []
    for implementation in load_implementations(registry_path):
        key = implementation.key
        basis: dict[str, WorkloadBasis] = {}
        for workload in shares:
            # ワークロードごとに、この言語を含む最新の計測を根拠にする。
            for summary in (run for run in runs if run["workload"] == workload):
                found = _basis_for_run(
                    results_dir / "raw" / summary["run_id"],
                    key,
                    memory_mb=memory_mb,
                    architecture=architecture,
                    pricing=pricing,
                )
                if found:
                    basis[workload] = replace(
                        found,
                        started_at=summary["started_at"],
                        image_digest=summary["image_digests"].get(key),
                    )
                    break
        missing = [workload for workload in shares if workload not in basis]
        languages.append(
            _language_estimate(
                key=key,
                label=implementation.label,
                shares=shares,
                basis=basis,
                missing=missing,
                cold_ratio=cold_ratio,
                monthly_requests=monthly_requests,
                memory_mb=memory_mb,
                architecture=architecture,
                pricing=pricing,
            )
        )

    return {
        "conditions": {
            "architecture": architecture,
            "memory_mb": memory_mb,
            "monthly_requests": monthly_requests,
            "cold_start_percent": cold_start_percent,
            "mix": {workload: share * 100 for workload, share in shares.items()},
        },
        "pricing": {"updated_at": pricing.updated_at, "region": pricing.region},
        "languages": languages,
    }


def _language_estimate(
    *,
    key: str,
    label: str,
    shares: dict[str, float],
    basis: dict[str, WorkloadBasis],
    missing: list[str],
    cold_ratio: float,
    monthly_requests: int,
    memory_mb: int,
    architecture: str,
    pricing: Pricing,
) -> dict[str, Any]:
    """一つの言語について、割合で重み付けした料金・処理時間を求める。"""
    sources = [
        {
            "workload": workload,
            "run_id": item.run_id,
            "started_at": item.started_at,
            "image_digest": item.image_digest,
        }
        for workload, item in basis.items()
    ]
    # 構成に含むワークロードの一つでも実測がなければ、一部だけの合算で誤った見込みを出さない。
    if missing:
        return {
            "key": key,
            "label": label,
            "estimable": False,
            "missing_workloads": missing,
            "reason": "missing",
            "sources": sources,
        }
    digests = {item.image_digest for item in basis.values()}
    # ワークロードごとの根拠が別の版（イメージ）なら、同じ実装の料金として合算できない。
    if None in digests or len(digests) > 1:
        return {
            "key": key,
            "label": label,
            "estimable": False,
            "missing_workloads": [],
            "reason": "inconsistent_versions",
            "sources": sources,
        }

    inits = [item.init_duration_ms for item in basis.values() if item.init_duration_ms is not None]
    # コールド時の実測がないワークロードを近似するための、言語の初期化時間（中央値）。
    language_init = statistics.median(inits) if inits else None

    per_request = 0.0
    warm_duration = 0.0
    billed_ms = 0.0
    approximated = []
    cold_excluded = []
    for workload, share in shares.items():
        item = basis[workload]
        cold_cost = item.cold_cost
        # コールドの実測があればそれを使う。なければ初期化時間で近似し、それも無ければ含めない。
        if cold_cost is None and language_init is not None:
            cold_cost = (
                item.warm_cost
                + calculate_cost(
                    billed_duration_ms=language_init,
                    memory_size_mb=memory_mb,
                    architecture=architecture,
                    pricing=pricing,
                )["compute_cost"]
            )
            if cold_ratio > 0:
                approximated.append(workload)
        elif cold_cost is None:
            cold_cost = item.warm_cost
            if cold_ratio > 0:
                cold_excluded.append(workload)
        per_request += share * ((1 - cold_ratio) * item.warm_cost + cold_ratio * cold_cost)
        warm_duration += share * item.warm_duration_ms
        # 使用量も料金と同じ近似で求める（コールドの実測がなければ初期化時間を足す）。
        cold_billed = item.cold_billed_ms
        if cold_billed is None:
            cold_billed = item.warm_billed_ms + (language_init or 0.0)
        billed_ms += share * ((1 - cold_ratio) * item.warm_billed_ms + cold_ratio * cold_billed)

    request_cost = float(pricing.request_price_per_million) / 1_000_000
    monthly_gb_seconds = billed_ms / 1000 * memory_mb / 1024 * monthly_requests
    limit = pricing.tier1_max_gb_seconds_per_month.get(architecture)
    return {
        "key": key,
        "label": label,
        "estimable": True,
        "monthly_cost": per_request * monthly_requests,
        "monthly_compute_cost": (per_request - request_cost) * monthly_requests,
        "warm_duration_ms": warm_duration,
        "init_duration_ms": language_init,
        "approximated_cold_workloads": approximated,
        "cold_excluded_workloads": cold_excluded,
        "monthly_gb_seconds": monthly_gb_seconds,
        # 料金表の単価を適用できる使用量の範囲内か。上限が分からない場合も範囲内とは扱わない。
        "within_pricing_tier": limit is not None and monthly_gb_seconds <= float(limit),
        "sources": sources,
    }
