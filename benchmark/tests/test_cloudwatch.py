"""Lambda REPORT解析と、構造化ログ・測定レコードの相関規則を検証する。"""

from benchmark_runner.cloudwatch import correlate_samples, parse_report


def test_parses_report_with_and_without_init_duration():
    cold = parse_report(
        "REPORT RequestId: req-1 Duration: 12.34 ms Billed Duration: 13 ms "
        "Memory Size: 512 MB Max Memory Used: 87 MB Init Duration: 101.2 ms"
    )
    warm = parse_report(
        "REPORT RequestId: req-2 Duration: 5.00 ms Billed Duration: 6 ms "
        "Memory Size: 512 MB Max Memory Used: 80 MB"
    )

    assert cold["init_duration_ms"] == 101.2
    assert warm["init_duration_ms"] is None


def test_correlates_by_request_id_and_run_id_and_preserves_nulls():
    samples = [
        {
            "run_id": "run-1",
            "implementation": "python",
            "request_index": 1,
            "success": True,
            "lambda_request_id": "req-1",
        },
        {
            "run_id": "run-1",
            "implementation": "python",
            "request_index": 2,
            "success": True,
            "lambda_request_id": None,
        },
    ]
    messages = [
        (
            '{"implementation":"python","lambda_request_id":"req-1",'
            '"benchmark_run_id":"run-1","route":"/health","status_code":200}'
        ),
        (
            "REPORT RequestId: req-1 Duration: 12 ms Billed Duration: 13 ms "
            "Memory Size: 256 MB Max Memory Used: 70 MB Init Duration: 90 ms"
        ),
        (
            '{"implementation":"python","lambda_request_id":"other",'
            '"benchmark_run_id":"another-run"}'
        ),
    ]

    merged, events = correlate_samples(samples, messages, run_id="run-1")

    assert len(events) == 2
    assert merged[0]["duration_ms"] == 12.0
    assert merged[0]["start_type"] == "cold"
    assert merged[0]["log_matched"] is True
    assert merged[1]["duration_ms"] is None
    assert merged[1]["start_type"] is None


def test_correlates_by_benchmark_request_index_when_response_has_no_lambda_id():
    samples = [
        {
            "run_id": "run-1",
            "implementation": "rust",
            "request_index": 2,
            "success": True,
            "lambda_request_id": None,
        }
    ]
    messages = [
        (
            '{"implementation":"rust","lambda_request_id":"req-2",'
            '"benchmark_run_id":"run-1","benchmark_request_index":2,'
            '"route":"/health","status_code":200}'
        ),
        (
            "REPORT RequestId: req-2 Duration: 7 ms Billed Duration: 8 ms "
            "Memory Size: 512 MB Max Memory Used: 64 MB"
        ),
    ]

    merged, events = correlate_samples(samples, messages, run_id="run-1")

    assert len(events) == 2
    assert merged[0]["lambda_request_id"] == "req-2"
    assert merged[0]["duration_ms"] == 7.0
    assert merged[0]["start_type"] == "warm"
    assert merged[0]["log_matched"] is True
    assert merged[0]["report_matched"] is True
