"""失敗した要求・成功内容の未確認・コールドスタートの根拠不足を、指標ごとの採否へ反映することを検証する。

レビューで報告された「全件502でも成功扱いになる」「200の不正本文を成功とする」再現を、
現在のコードで回帰テストとして残す（過去の再現スクリプトは一時領域から失われている）。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from benchmark_runner.report import build_report, generate_report
from benchmark_runner.response_contract import validate_success_response
from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.workloads import Workload

PRICING = Path(__file__).resolve().parents[2] / "spec/pricing.yaml"
REGISTRY = Path(__file__).parent / "fixtures/implementations.yaml"


def _record(implementation, index, *, success=True, start_type="warm", checked=True, init=None):
    return {
        "implementation": implementation,
        "request_index": index,
        "success": success,
        "response_checked": checked,
        "latency_ms": 10.0 + index,
        "duration_ms": 5.0,
        "billed_duration_ms": 6.0,
        "init_duration_ms": init,
        "memory_size_mb": 512,
        "max_memory_used_mb": 80,
        "start_type": start_type,
    }


def _write_run(results, records, *, region="ap-northeast-1", run_id="run"):
    run_dir = results / "raw" / run_id
    run_dir.mkdir(parents=True)
    metadata = {
        "run_id": run_id,
        "started_at": "2026-10-01T00:00:00+00:00",
        "completed_at": "2026-10-01T00:01:00+00:00",
        "architecture": "arm64",
        "memory_mb": 512,
        "region": region,
        "dry_run": False,
        "verification": {"mode": "aws"},
        "workload": {"name": "health", "iterations": 2},
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    lines = "".join(json.dumps(record) + "\n" for record in records)
    (run_dir / "samples.jsonl").write_text(lines, encoding="utf-8")
    (run_dir / "merged.jsonl").write_text(lines, encoding="utf-8")


def _report(tmp_path, records, **options):
    _write_run(tmp_path, records, **options)
    return build_report(
        run_id=options.get("run_id", "run"),
        results_dir=tmp_path,
        pricing_path=PRICING,
        registry_path=REGISTRY,
    )


def _measure(tmp_path, handler, path="/health"):
    workload = Workload("health", "GET", path, 0, 3, 1)
    config = RunConfig(
        run_id="measured",
        targets=("python",),
        endpoints={"python": "https://example.test"},
        region="ap-northeast-1",
        results_dir=tmp_path,
        sign_requests=False,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(run_benchmark(config, workload))
    lines = (tmp_path / "raw/measured/samples.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_runner_records_every_502_as_failure(tmp_path):
    records = _measure(tmp_path, lambda request: httpx.Response(502, json={"message": "Bad"}))

    assert [record["success"] for record in records] == [False, False, False]
    assert all(record["error"] == "HTTP状態 502" for record in records)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json={"status": "degraded"}),
        httpx.Response(200, json={"error": {"code": "INTERNAL_ERROR"}}),
        httpx.Response(204),
    ],
)
def test_runner_rejects_200_without_contract_body(tmp_path, response):
    records = _measure(tmp_path, lambda request: response)

    assert not any(record["success"] for record in records)
    assert all(record["response_checked"] for record in records)


def test_runner_accepts_contract_body(tmp_path):
    records = _measure(tmp_path, lambda request: httpx.Response(200, json={"status": "ok"}))

    assert all(record["success"] and record["error"] is None for record in records)


@pytest.mark.parametrize(
    ("path", "body", "valid"),
    [
        ("/transform", {"records": [{"id": "a"}], "count": 1}, True),
        ("/transform", {"records": [{"id": "a"}], "count": 2}, False),
        ("/compute", {"processed": 1, "iterations": 1, "checksum": "a" * 64}, True),
        ("/compute", {"processed": 1, "iterations": 1, "checksum": "xyz"}, False),
        ("/items/item-001", {"item": {"id": "item-001", "value": 1, "payload": ""}}, True),
        ("/items/item-001", {"item": {"id": "item-001"}}, False),
        ("/aggregate", {"count": 5, "total_value": 10, "checksum": "0" * 64}, True),
        ("/aggregate", {"count": 5, "total_value": 1.5, "checksum": "0" * 64}, False),
    ],
)
def test_success_body_follows_contract_per_route(path, body, valid):
    reason = validate_success_response(path, 200, json.dumps(body).encode())

    assert (reason is None) is valid


def test_all_failed_language_makes_every_metric_unusable(tmp_path):
    records = [_record("python", index) for index in (1, 2)] + [
        _record("rust", index, success=False, start_type=None) for index in (1, 2)
    ]

    quality = _report(tmp_path, records)["quality"]

    assert quality["comparable"] is False
    assert {column["status"] for column in quality["columns"].values()} == {"unusable"}
    assert "Rust はすべての要求が失敗" in "\n".join(quality["issues"])


def test_mixed_run_keeps_success_statistics_as_reference(tmp_path):
    records = [_record("python", index) for index in (1, 2, 3)] + [
        _record("rust", 1),
        _record("rust", 2),
        _record("rust", 3, success=False, start_type=None),
    ]

    report = _report(tmp_path, records)
    rust = report["implementations"]["rust"]

    assert rust["success_count"] == 2 and rust["error_count"] == 1
    assert rust["metrics"]["latency_ms"]["count"] == 2
    # 失敗した要求にも課金されうるため、成功の料金とは別に保存する。
    assert rust["failed_cost"]["priced_sample_count"] == 1
    assert rust["cost"]["priced_sample_count"] == 2
    assert report["quality"]["columns"]["duration"]["status"] == "reference"
    assert report["quality"]["comparable"] is False
    assert "3件中1件" in "\n".join(report["quality"]["issues"])


def test_run_measured_before_body_check_is_reference(tmp_path):
    records = [
        _record(name, index, checked=False) for name in ("python", "rust") for index in (1, 2)
    ]

    quality = _report(tmp_path, records)["quality"]

    assert quality["comparable"] is False
    assert quality["columns"]["latency"]["status"] == "reference"
    assert "応答の内容を確かめる前の版" in "\n".join(quality["issues"])


@pytest.mark.parametrize(
    ("python_cold", "rust_cold", "status"),
    [(0, 0, "unusable"), (1, 0, "unusable"), (1, 1, "reference"), (2, 2, "reference")],
)
def test_cold_start_metric_is_never_a_formal_basis_with_incidental_samples(
    tmp_path, python_cold, rust_cold, status
):
    records = []
    for name, cold in (("python", python_cold), ("rust", rust_cold)):
        records += [_record(name, index) for index in range(1, 21)]
        records += [
            _record(name, 100 + index, start_type="cold", init=200.0) for index in range(cold)
        ]

    quality = _report(tmp_path, records)["quality"]

    assert quality["columns"]["init"]["status"] == status
    # ウォームの指標はコールドスタートの件数と独立に判定する。
    assert quality["columns"]["duration"]["status"] == "usable"
    assert quality["comparable"] is True


def test_pricing_of_another_region_is_not_applied(tmp_path):
    records = [_record(name, index) for name in ("python", "rust") for index in (1, 2)]

    report = _report(tmp_path, records, region="us-east-1")

    assert report["implementations"]["python"]["cost"] is None
    assert report["quality"]["columns"]["cost"]["status"] == "unusable"
    assert "地域" in report["pricing_note"]
    # 料金を出せなくても、性能の比較は続けられる。
    assert report["quality"]["comparable"] is True


def test_markdown_report_shows_success_counts_and_metric_status(tmp_path):
    records = [_record("python", 1), _record("python", 2, success=False, start_type=None)]
    _write_run(tmp_path, records + [_record("rust", 1)])

    markdown, _ = generate_report(run_id="run", results_dir=tmp_path, pricing_path=PRICING)

    content = markdown.read_text(encoding="utf-8")
    assert "| Implementation | Samples | Succeeded | Failed |" in content
    assert "## Comparability（比較に使えるか）" in content
    assert "reference" in content


def test_cli_run_fails_when_a_language_has_no_success():
    from benchmark_runner.cli import _run_exit_code

    assert _run_exit_code({"python": {"success_count": 0, "failure_count": 3}}) == 1
    assert _run_exit_code({"python": {"success_count": 2, "failure_count": 1}}) == 0
    assert _run_exit_code({"python": {"success_count": 3, "failure_count": 0}}) == 0


def test_local_unsigned_run_is_reference_not_formal(tmp_path):
    records = [_record(name, index) for name in ("python", "rust") for index in (1, 2)]
    _write_run(tmp_path, records)
    metadata_path = tmp_path / "raw/run/metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["verification"] = {"mode": "local"}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    report = build_report(
        run_id="run", results_dir=tmp_path, pricing_path=PRICING, registry_path=REGISTRY
    )

    assert report["quality"]["comparable"] is False
    assert "ローカル" in "\n".join(report["quality"]["issues"])


def test_aborted_run_is_not_comparable(tmp_path):
    records = [_record(name, index) for name in ("python", "rust") for index in (1, 2)]
    _write_run(tmp_path, records)
    metadata_path = tmp_path / "raw/run/metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["aborted"] = "署名に失敗したため計測を中断しました"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    report = build_report(
        run_id="run", results_dir=tmp_path, pricing_path=PRICING, registry_path=REGISTRY
    )

    assert report["quality"]["comparable"] is False
    assert "中断" in "\n".join(report["quality"]["issues"])


def test_reason_that_decides_the_status_comes_first(tmp_path):
    # 失敗（参考値）とコールドスタートの観測なし（比較不可）が重なる場合、重い理由を先に示す。
    records = [_record(name, index) for name in ("python", "rust") for index in (1, 2)]
    records.append(_record("rust", 3, success=False, start_type=None))

    init = _report(tmp_path, records)["quality"]["columns"]["init"]

    assert init["status"] == "unusable"
    assert "コールドスタートを観測していません" in init["reasons"][0]
