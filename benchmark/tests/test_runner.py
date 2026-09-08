import asyncio
import json

import httpx

from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.workloads import Workload


def test_runner_warms_up_and_writes_measured_samples(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        index = request.headers["X-Benchmark-Request-Index"]
        return httpx.Response(
            200,
            json={"lambda_request_id": f"request-{index}"},
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
        target="python",
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
        target="both",
        endpoints={},
        region="ap-northeast-1",
        results_dir=tmp_path,
        dry_run=True,
    )

    plan = asyncio.run(run_benchmark(config, workload))

    assert plan["targets"] == ["python", "rust"]
    assert not (tmp_path / "raw").exists()
