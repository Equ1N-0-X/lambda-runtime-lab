"""作りたいAPIの見込みが、計測結果と同じ料金式で構成割合を重み付けし、根拠と欠損を示すことを検証する。"""

import json
from pathlib import Path

import pytest

from benchmark_runner.dashboard.estimate import EstimateError, available_conditions, build_estimate

REGISTRY = Path(__file__).parent / "fixtures/implementations.yaml"
PRICE = 0.0000133334
REQUEST = 0.20 / 1_000_000


def _pricing(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "version: 1\nupdated_at: 2026-09-11\nregion: ap-northeast-1\nsource: test\n"
        f"request_price_per_million: 0.20\ncompute_price_per_gb_second:\n  arm64: {PRICE}\n",
        encoding="utf-8",
    )
    return path


def _record(language, index, *, billed, start_type="warm", init=None):
    return {
        "implementation": language,
        "request_index": index,
        "success": True,
        "latency_ms": 10.0,
        "duration_ms": billed - 0.5,
        "billed_duration_ms": billed,
        "init_duration_ms": init,
        "memory_size_mb": 1024,
        "max_memory_used_mb": 80,
        "start_type": start_type,
    }


def _write_run(results, run_id, workload, records, started_at="2026-10-01T00:00:00+00:00"):
    run_dir = results / "raw" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "started_at": started_at,
                "architecture": "arm64",
                "memory_mb": 1024,
                "dry_run": False,
                "workload": {"name": workload, "iterations": 2},
            }
        ),
        encoding="utf-8",
    )
    lines = "".join(json.dumps(record) + "\n" for record in records)
    (run_dir / "samples.jsonl").write_text(lines, encoding="utf-8")
    (run_dir / "merged.jsonl").write_text(lines, encoding="utf-8")


def _cost(billed_ms):
    # メモリ1024MB（1GB）のため、実行時間分の料金は 秒 × GB秒単価 になる。
    return billed_ms / 1000 * PRICE + REQUEST


def _estimate(tmp_path, **overrides):
    values = {
        "mix": {"health": 25, "aggregate": 75},
        "monthly_requests": 1_000_000,
        "cold_start_percent": 0,
        "architecture": "arm64",
        "memory_mb": 1024,
        **overrides,
    }
    return build_estimate(
        **values,
        results_dir=tmp_path / "results",
        pricing_path=_pricing(tmp_path),
        registry_path=REGISTRY,
    )


@pytest.fixture
def results(tmp_path):
    results = tmp_path / "results"
    _write_run(
        results,
        "health",
        "health",
        [
            _record("python", 1, billed=10),
            _record("python", 2, billed=10),
            _record("python", 3, billed=110, start_type="cold", init=100),
            _record("rust", 1, billed=2),
            _record("rust", 2, billed=2),
        ],
    )
    _write_run(
        results,
        "aggregate",
        "aggregate",
        [
            _record("python", 1, billed=40),
            _record("python", 2, billed=40),
            _record("rust", 1, billed=8),
            _record("rust", 2, billed=8),
            _record("rust", 3, billed=20, start_type="cold", init=12),
        ],
    )
    return results


def test_weights_warm_costs_by_mix_with_the_same_formula_as_reports(tmp_path, results):
    languages = {item["key"]: item for item in _estimate(tmp_path)["languages"]}

    python = languages["python"]
    assert python["monthly_cost"] == pytest.approx(
        (0.25 * _cost(10) + 0.75 * _cost(40)) * 1_000_000
    )
    assert python["monthly_compute_cost"] == pytest.approx(
        (0.25 * _cost(10) + 0.75 * _cost(40) - REQUEST) * 1_000_000
    )
    assert python["warm_duration_ms"] == pytest.approx(0.25 * 9.5 + 0.75 * 39.5)
    assert {source["workload"] for source in python["sources"]} == {"health", "aggregate"}


def test_cold_start_share_uses_measured_cold_costs_or_init_approximation(tmp_path, results):
    languages = {
        item["key"]: item for item in _estimate(tmp_path, cold_start_percent=10)["languages"]
    }

    python = languages["python"]
    # healthはコールドの実測（110ms）を使い、aggregateは初期化100msぶんを足して近似する。
    expected = 0.25 * (0.9 * _cost(10) + 0.1 * _cost(110)) + 0.75 * (
        0.9 * _cost(40) + 0.1 * (_cost(40) + 100 / 1000 * PRICE)
    )
    assert python["monthly_cost"] == pytest.approx(expected * 1_000_000)
    assert python["approximated_cold_workloads"] == ["aggregate"]
    assert python["cold_excluded_workloads"] == []
    assert python["init_duration_ms"] == 100


def test_language_without_any_init_measurement_reports_cold_start_as_excluded(tmp_path):
    results = tmp_path / "results"
    _write_run(
        results,
        "health",
        "health",
        [_record("python", 1, billed=10), _record("rust", 1, billed=2)],
    )

    estimate = _estimate(tmp_path, mix={"health": 100}, cold_start_percent=10)
    python = {item["key"]: item for item in estimate["languages"]}["python"]

    # 初期化時間の実測がなければ近似できないため、近似したと示さず、含めていないことを示す。
    assert python["monthly_cost"] == pytest.approx(_cost(10) * 1_000_000)
    assert python["approximated_cold_workloads"] == []
    assert python["cold_excluded_workloads"] == ["health"]
    assert python["init_duration_ms"] is None


def test_language_without_measurements_for_a_workload_is_not_estimated(tmp_path, results):
    estimate = _estimate(tmp_path, mix={"health": 50, "never-measured": 50})

    assert all(not item["estimable"] for item in estimate["languages"])
    assert estimate["languages"][0]["missing_workloads"] == ["never-measured"]


def test_only_runs_with_the_selected_conditions_are_used(tmp_path, results):
    estimate = _estimate(tmp_path, memory_mb=512)

    assert all(not item["estimable"] for item in estimate["languages"])
    assert available_conditions(results_dir=results, pricing_path=_pricing(tmp_path)) == [
        {"architecture": "arm64", "memory_mb": 1024, "run_count": 2}
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mix": {"health": 50, "aggregate": 40}}, "100%"),
        ({"mix": {"health": "50"}}, "数値"),
        ({"monthly_requests": 0}, "月間リクエスト数"),
        ({"cold_start_percent": 101}, "コールドスタート"),
        ({"memory_mb": None}, "条件"),
    ],
)
def test_rejects_invalid_inputs_with_actionable_messages(tmp_path, results, overrides, message):
    with pytest.raises(EstimateError, match=message):
        _estimate(tmp_path, **overrides)
