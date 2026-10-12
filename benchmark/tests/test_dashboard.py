"""閲覧画面が比較可否を正しく判定し、環境情報を返さず、不正なrun IDを拒否することを検証する。"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from benchmark_runner.dashboard.catalog import RunNotFound, list_runs, load_run
from benchmark_runner.dashboard.jobs import JobManager, LaunchSettings
from benchmark_runner.dashboard.server import create_server


def _record(implementation, index, *, start_type="warm"):
    return {
        "run_id": "run",
        "implementation": implementation,
        "request_index": index,
        "success": True,
        "latency_ms": 10.0 + index,
        "duration_ms": 5.0,
        "billed_duration_ms": 6.0,
        "init_duration_ms": None,
        "memory_size_mb": 512,
        "max_memory_used_mb": 80,
        "start_type": start_type,
    }


def _write_run(results_dir, run_id, *, metadata_overrides=None, records=None, collected=True):
    run_dir = results_dir / "raw" / run_id
    run_dir.mkdir(parents=True)
    metadata = {
        "run_id": run_id,
        "started_at": "2026-10-01T00:00:00+00:00",
        "completed_at": "2026-10-01T00:01:00+00:00",
        "architecture": "arm64",
        "memory_mb": 512,
        "region": "ap-northeast-1",
        "dry_run": False,
        "endpoints": {"python": "https://secret.example", "rust": "https://secret.example"},
        "workload": {"name": "health", "iterations": 2, "client_concurrency": 1, "warmup": 0},
        **(metadata_overrides or {}),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if records is None:
        records = [_record(name, index) for name in ("python", "rust") for index in (1, 2)]
    lines = "".join(json.dumps(record) + "\n" for record in records)
    (run_dir / "samples.jsonl").write_text(lines, encoding="utf-8")
    if collected:
        (run_dir / "merged.jsonl").write_text(lines, encoding="utf-8")
    return run_dir


def test_load_run_marks_complete_run_comparable_and_hides_endpoints(tmp_path):
    _write_run(tmp_path, "complete")

    run = load_run("complete", results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml")

    assert run["comparable"] is True
    assert run["issues"] == []
    assert "endpoints" not in run["metadata"]
    assert "secret.example" not in json.dumps(run)
    assert run["differences"]["latency_ms"]["p50"] == pytest.approx(0.0)


def test_load_run_lists_reasons_when_conditions_or_logs_are_missing(tmp_path):
    _write_run(
        tmp_path,
        "incomplete",
        metadata_overrides={"architecture": None, "memory_mb": None},
        records=[_record("python", 1, start_type=None)],
        collected=False,
    )

    run = load_run("incomplete", results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml")

    assert run["comparable"] is False
    issues = "\n".join(run["issues"])
    assert "片方しか計測していない" in issues
    assert "アーキテクチャまたはメモリ設定が記録されていません" in issues
    assert "ログを収集していません" in issues
    assert run["differences"] is None


def test_load_run_reports_unmatched_samples_after_collection(tmp_path):
    records = [_record("python", 1), _record("rust", 1, start_type=None)]
    _write_run(tmp_path, "unmatched", records=records)

    run = load_run("unmatched", results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml")

    assert any("1件あります" in issue for issue in run["issues"])


@pytest.mark.parametrize("run_id", ["../secret", "missing", ".hidden", ""])
def test_load_run_rejects_unknown_or_unsafe_ids(tmp_path, run_id):
    _write_run(tmp_path, "complete")

    with pytest.raises(RunNotFound):
        load_run(run_id, results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml")


def test_list_runs_sorts_newest_first_and_skips_non_run_directories(tmp_path):
    _write_run(tmp_path, "older", metadata_overrides={"started_at": "2026-09-01T00:00:00+00:00"})
    _write_run(tmp_path, "newer", metadata_overrides={"started_at": "2026-10-05T00:00:00+00:00"})
    (tmp_path / "raw" / "image-build-20260916").mkdir()

    runs = list_runs(results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml")

    assert [run["run_id"] for run in runs] == ["newer", "older"]


def test_list_runs_keeps_listing_when_one_run_is_broken(tmp_path):
    _write_run(tmp_path, "complete")
    broken = _write_run(tmp_path, "broken")
    (broken / "merged.jsonl").write_text("not json\n", encoding="utf-8")

    runs = {
        run["run_id"]: run for run in list_runs(results_dir=tmp_path, pricing_path=tmp_path / "x")
    }

    assert runs["complete"]["comparable"] is True
    assert runs["broken"]["comparable"] is False
    assert "読み込めません" in runs["broken"]["issues"][0]


def test_list_runs_returns_empty_when_nothing_measured(tmp_path):
    assert list_runs(results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml") == []


@pytest.fixture
def server_url(tmp_path):
    _write_run(tmp_path, "complete")
    jobs = JobManager(
        settings=LaunchSettings.from_environment({}),
        workload_file=tmp_path / "workloads.yaml",
        results_dir=tmp_path,
        pricing_path=tmp_path / "missing.yaml",
    )
    server = create_server(
        port=0, results_dir=tmp_path, pricing_path=tmp_path / "missing.yaml", jobs=jobs
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers["Content-Type"], response.read()


def test_server_serves_page_and_run_api(server_url):
    status, content_type, body = _get(f"{server_url}/")
    assert status == 200
    assert content_type.startswith("text/html")
    assert "Lambda ベンチマーク結果".encode() in body

    _, _, runs_body = _get(f"{server_url}/api/runs")
    assert json.loads(runs_body)["runs"][0]["run_id"] == "complete"

    _, _, run_body = _get(f"{server_url}/api/runs/complete")
    assert json.loads(run_body)["comparable"] is True


@pytest.mark.parametrize("path", ["/api/runs/missing", "/api/runs/..%2Fsecret", "/etc/passwd"])
def test_server_returns_404_for_unknown_paths(server_url, path):
    with pytest.raises(urllib.error.HTTPError) as error:
        _get(f"{server_url}{path}")

    assert error.value.code == 404
    assert "error" in json.loads(error.value.read())
