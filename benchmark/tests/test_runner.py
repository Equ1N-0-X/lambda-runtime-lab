"""対象APIへの要求回数、相関ヘッダー、測定値、保存成果物を検証する。"""

import asyncio
import json

import httpx
import pytest

from benchmark_runner.implementations import load_implementations, resolve_targets
from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.workloads import Workload


def test_runner_warms_up_and_writes_measured_samples(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        index = request.headers["X-Benchmark-Request-Index"]
        return httpx.Response(
            200,
            json={"status": "ok", "lambda_request_id": f"request-{index}"},
        )

    workload = Workload(
        name="health",
        method="GET",
        path="/health",
        warmup=1,
        iterations=2,
        client_concurrency=2,
    )
    config = RunConfig(
        run_id="test-run",
        targets=("python",),
        endpoints={"python": "https://example.test"},
        region="ap-northeast-1",
        results_dir=tmp_path,
        sign_requests=False,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(run_benchmark(config, workload))

    assert result["sample_count"] == 2
    assert len(requests) == 3
    assert {request.headers["X-Benchmark-Run-Id"] for request in requests} == {"test-run"}
    records = [
        json.loads(line)
        for line in (tmp_path / "raw/test-run/samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["request_index"] for record in records] == [1, 2]
    assert all(record["success"] for record in records)


def test_dry_run_requires_no_endpoint_or_network(tmp_path):
    workload = Workload("health", "GET", "/health", 0, 1, 1)
    config = RunConfig(
        run_id="dry-run",
        targets=("python", "rust"),
        endpoints={},
        region="ap-northeast-1",
        results_dir=tmp_path,
        dry_run=True,
    )

    plan = asyncio.run(run_benchmark(config, workload))

    assert plan["targets"] == ["python", "rust"]
    assert not (tmp_path / "raw").exists()


def test_resolve_targets_follows_registry_order_and_rejects_unknown_keys():
    available = ("python", "rust", "typescript", "java")

    assert resolve_targets("all", available) == available
    assert resolve_targets("java, python", available) == ("python", "java")
    with pytest.raises(ValueError, match="cobol"):
        resolve_targets("python,cobol", available)


def test_registry_lists_implementations_in_display_order():
    keys = [implementation.key for implementation in load_implementations()]

    assert keys[:2] == ["python", "rust"]
    assert len(keys) == len(set(keys))


class _FakeColdStarter:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def force(self, function_name):
        from benchmark_runner.cold_start import ColdStartError

        self.calls.append(("force", function_name))
        if self.calls.count(("force", function_name)) == self.fail_on:
            raise ColdStartError("権限がありません")

    def restore(self, function_name):
        self.calls.append(("restore", function_name))


def _cold_config(tmp_path, starter, requests):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    return RunConfig(
        run_id="cold-run",
        targets=("python", "rust"),
        endpoints={"python": "https://python.test", "rust": "https://rust.test"},
        region="ap-northeast-1",
        results_dir=tmp_path,
        sign_requests=False,
        transport=httpx.MockTransport(handler),
        function_names={"python": "fn-python", "rust": "fn-rust"},
        cold_starter=starter,
    )


COLD_WORKLOAD = Workload("cold-start", "GET", "/health", 0, 3, 1, cold_start=True)


def test_cold_start_run_forces_a_new_environment_before_every_request(tmp_path):
    starter, requests = _FakeColdStarter(), []

    result = asyncio.run(run_benchmark(_cold_config(tmp_path, starter, requests), COLD_WORKLOAD))

    # 要求ごとに一度ずつ設定を更新し、最後に一度だけ元へ戻す。
    for name in ("fn-python", "fn-rust"):
        calls = [call for call in starter.calls if call[1] == name]
        assert calls == [("force", name)] * 3 + [("restore", name)]
    assert len(requests) == 6
    records = [
        json.loads(line)
        for line in (tmp_path / "raw/cold-run/samples.jsonl").read_text().splitlines()
    ]
    # 同時に測った結果を、実装一覧の順・要求番号の順にそろえて保存する。
    assert [(r["implementation"], r["request_index"]) for r in records] == [
        ("python", 1),
        ("python", 2),
        ("python", 3),
        ("rust", 1),
        ("rust", 2),
        ("rust", 3),
    ]
    assert all(record["forced_cold_start"] for record in records)
    assert result["outcomes"]["rust"]["success_count"] == 3


def test_cold_start_run_restores_settings_when_it_aborts(tmp_path):
    from benchmark_runner.cold_start import ColdStartError

    starter, requests = _FakeColdStarter(fail_on=2), []

    with pytest.raises(ColdStartError):
        asyncio.run(run_benchmark(_cold_config(tmp_path, starter, requests), COLD_WORKLOAD))

    # 失敗しても、更新した全関数の設定を元へ戻す。
    assert ("restore", "fn-python") in starter.calls
    assert ("restore", "fn-rust") in starter.calls
    metadata = json.loads((tmp_path / "raw/cold-run/metadata.json").read_text())
    assert "Lambda の設定の更新に失敗" in metadata["aborted"]


def test_cold_start_run_requires_function_names(tmp_path):
    config = _cold_config(tmp_path, _FakeColdStarter(), [])
    config = RunConfig(**{**config.__dict__, "function_names": {"python": "fn-python"}})

    with pytest.raises(ValueError, match="Lambda関数名"):
        asyncio.run(run_benchmark(config, COLD_WORKLOAD))
