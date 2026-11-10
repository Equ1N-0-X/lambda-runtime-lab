"""ログ収集が取得上限で末尾を欠かさず、反映の遅れを期限付きで待ち、欠けを完了と装わないことを検証する。"""

import json

import pytest

from benchmark_runner.cloudwatch import INSIGHTS_RESULT_LIMIT, InsightsTruncated, collect_run

RUN_ID = "run-1"


def _report(request_id, billed=6):
    return (
        f"REPORT RequestId: {request_id} Duration: 5.00 ms Billed Duration: {billed} ms "
        "Memory Size: 512 MB Max Memory Used: 80 MB"
    )


def _app_log(request_id, index):
    return json.dumps(
        {
            "benchmark_run_id": RUN_ID,
            "benchmark_request_index": index,
            "lambda_request_id": request_id,
        }
    )


def _write_run(results, samples):
    run_dir = results / "raw" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "started_at": "2026-10-01T00:00:00+00:00",
                "completed_at": "2026-10-01T00:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "samples.jsonl").write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8"
    )
    return run_dir


def _sample(index, *, success=True):
    # API Gatewayの応答にLambdaのIDがない場合を再現し、計測番号とアプリログで結び付ける。
    return {
        "run_id": RUN_ID,
        "implementation": "python",
        "request_index": index,
        "success": success,
        "lambda_request_id": None,
    }


class FakeLogs:
    """Insightsの結果と、filter_log_eventsのページを、取得回数ごとに切り替えて返す。"""

    def __init__(self, *, insights=None, pages=None):
        self.insights = insights or []
        self.pages = pages or []
        self.queries = []
        self.filter_calls = 0
        self.attempt = 0

    def start_query(self, **arguments):
        self.queries.append(arguments)
        return {"queryId": "q"}

    def get_query_results(self, queryId):  # noqa: N803 - boto3の引数名
        messages = self.insights[min(self.attempt, len(self.insights) - 1)]
        self.attempt += 1
        if isinstance(messages, Exception):
            raise messages
        return {
            "status": "Complete",
            "results": [[{"field": "@message", "value": message}] for message in messages],
        }

    def filter_log_events(self, **arguments):
        self.filter_calls += 1
        index = int(arguments.get("nextToken") or 0)
        response = {"events": [{"message": message} for message in self.pages[index]]}
        if index + 1 < len(self.pages):
            response["nextToken"] = str(index + 1)
        return response


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _collect(tmp_path, logs, *, wait_seconds=0, strategy="auto"):
    clock = Clock()
    result = collect_run(
        run_id=RUN_ID,
        results_dir=tmp_path,
        log_groups={"python": "/aws/lambda/python"},
        logs_client=logs,
        strategy=strategy,
        wait_seconds=wait_seconds,
        poll_seconds=10,
        clock=clock,
        sleep=clock.sleep,
    )
    merged = [
        json.loads(line)
        for line in (tmp_path / "raw" / RUN_ID / "merged.jsonl").read_text().splitlines()
    ]
    return result, merged, clock


def test_query_keeps_reports_and_this_runs_app_logs(tmp_path):
    _write_run(tmp_path, [_sample(1)])
    logs = FakeLogs(insights=[[_app_log("req-1", 1), _report("req-1")]])

    result, merged, _ = _collect(tmp_path, logs)

    query = logs.queries[0]["queryString"]
    # REPORTだけに絞ると計測番号から結び付けられないため、このrunのアプリログも残す。
    assert "REPORT RequestId" in query and RUN_ID in query
    assert merged[0]["report_matched"] is True
    assert result["status"] == "complete"


def test_truncated_insights_result_falls_back_to_full_pagination(tmp_path):
    _write_run(tmp_path, [_sample(1), _sample(2)])
    # 上限ちょうどの結果には、末尾の計測（req-2）のREPORTが含まれていない。
    head = [_app_log("req-1", 1), _report("req-1")]
    truncated = head + ["START RequestId: filler"] * (INSIGHTS_RESULT_LIMIT - len(head))
    tail = [_app_log("req-2", 2), _report("req-2")]
    logs = FakeLogs(insights=[truncated], pages=[head, tail])

    result, merged, _ = _collect(tmp_path, logs)

    assert logs.filter_calls == 2
    assert [row["report_matched"] for row in merged] == [True, True]
    status = result["implementations"]["python"]
    assert status["source"] == "filter"
    assert "上限" in status["fallback_reason"]


def test_truncated_result_is_an_error_when_insights_is_required(tmp_path):
    _write_run(tmp_path, [_sample(1)])
    logs = FakeLogs(insights=[["x"] * INSIGHTS_RESULT_LIMIT])

    with pytest.raises(InsightsTruncated):
        _collect(tmp_path, logs, strategy="insights")


def test_delayed_reports_are_collected_again_until_complete(tmp_path):
    _write_run(tmp_path, [_sample(1)])
    # 1回目はアプリログだけが反映済みで、REPORTは2回目に届く。
    logs = FakeLogs(insights=[[_app_log("req-1", 1)], [_app_log("req-1", 1), _report("req-1")]])

    result, merged, clock = _collect(tmp_path, logs, wait_seconds=60)

    assert result["status"] == "complete"
    assert result["attempts"] == 2
    assert clock.sleeps == [10]
    assert merged[0]["report_matched"] is True


def test_complete_collection_does_not_wait(tmp_path):
    _write_run(tmp_path, [_sample(1)])
    logs = FakeLogs(insights=[[_app_log("req-1", 1), _report("req-1")]])

    result, _, clock = _collect(tmp_path, logs, wait_seconds=60)

    assert result["attempts"] == 1
    assert clock.sleeps == []


def test_reports_still_missing_at_deadline_are_reported_as_incomplete(tmp_path):
    _write_run(tmp_path, [_sample(1), _sample(2)])
    logs = FakeLogs(insights=[[_app_log("req-1", 1), _report("req-1")]])

    result, merged, clock = _collect(tmp_path, logs, wait_seconds=25)

    assert result["status"] == "incomplete"
    assert result["missing_reports"] == 1
    # 期限（25秒）を超えて待たず、欠けた値を0で埋めずに未結合のまま残す。
    assert sum(clock.sleeps) == 25
    assert merged[1]["billed_duration_ms"] is None
    collection = json.loads((tmp_path / "raw" / RUN_ID / "collection.json").read_text())
    assert collection["status"] == "incomplete"


def test_failed_requests_without_report_do_not_block_completion(tmp_path):
    # API Gatewayで止まった失敗はLambdaが動いておらず、REPORTが存在しない。
    _write_run(tmp_path, [_sample(1), _sample(2, success=False)])
    logs = FakeLogs(insights=[[_app_log("req-1", 1), _report("req-1")]])

    result, _, clock = _collect(tmp_path, logs, wait_seconds=60)

    assert result["status"] == "complete"
    assert clock.sleeps == []


def test_cli_collect_exits_with_3_when_logs_are_incomplete(tmp_path, monkeypatch, capsys):
    from benchmark_runner import cli

    monkeypatch.setattr(
        cli,
        "collect_run",
        lambda **kwargs: {"status": "incomplete", "missing_reports": 2},
    )
    monkeypatch.setattr(cli.boto3, "client", lambda *args, **kwargs: object())

    code = cli.main(["collect", "--run-id", RUN_ID, "--log-group", "python=/aws/lambda/python"])

    assert code == 3
    assert "2 件" in capsys.readouterr().err
