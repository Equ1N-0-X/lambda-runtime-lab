"""百分位点、Cold/Warm分類、エラー率の集計規則を検証する。"""

from benchmark_runner.analyzer import percentile, summarize


def test_percentile_uses_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.5) == 2.5
    assert percentile(values, 0.95) == 3.8499999999999996
    assert percentile([], 0.5) is None


def test_summary_separates_cold_warm_and_errors():
    records = [
        {
            "implementation": "python",
            "success": True,
            "start_type": "cold",
            "latency_ms": 10,
            "duration_ms": 8,
        },
        {
            "implementation": "python",
            "success": False,
            "start_type": "warm",
            "latency_ms": 20,
            "duration_ms": None,
        },
        {
            "implementation": "python",
            "success": True,
            "start_type": None,
            "latency_ms": 30,
            "duration_ms": None,
        },
    ]

    result = summarize(records)["implementations"]["python"]

    assert result["sample_count"] == 3
    assert result["success_count"] == 2
    assert result["error_count"] == 1
    assert result["error_rate"] == 1 / 3
    # 起動の種類と統計は成功した要求だけで数え、失敗は別の統計に分ける。
    assert result["cold_count"] == 1
    assert result["warm_count"] == 0
    assert result["unclassified_count"] == 1
    assert result["metrics"]["latency_ms"]["p50"] == 20
    assert result["metrics"]["latency_ms"]["count"] == 2
    assert result["failed_metrics"]["latency_ms"]["p50"] == 20
    assert result["metrics"]["duration_ms"]["count"] == 1


def test_failed_requests_do_not_enter_success_statistics():
    records = [
        {"implementation": "python", "success": False, "latency_ms": 1, "start_type": "warm"},
        {"implementation": "python", "success": True, "latency_ms": 50, "start_type": "warm"},
        # 真偽値以外のsuccessは成功とみなさない。
        {"implementation": "python", "success": "true", "latency_ms": 2, "start_type": "warm"},
    ]

    result = summarize(records)["implementations"]["python"]

    assert result["success_count"] == 1
    assert result["metrics"]["latency_ms"]["p50"] == 50
    assert result["response_checked"] is False
