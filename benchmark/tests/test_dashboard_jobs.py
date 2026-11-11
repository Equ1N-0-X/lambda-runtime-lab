"""画面からの計測実行が入力を検証し、一件ずつ工程を進め、失敗・中止・不正な要求を正しく扱うことを検証する。"""

import http.client
import json
import sys
import threading
import time

import pytest

from benchmark_runner.dashboard.jobs import JobConflict, JobManager, LaunchError
from benchmark_runner.dashboard.server import create_server
from benchmark_runner.dashboard.settings import LaunchSettings
from benchmark_runner.implementations import Implementation

SECRET_URL = "https://secret-api.example"

WORKLOADS = """
version: 1
workloads:
  health:
    method: GET
    path: /health
    warmup: 2
    iterations: 5
    client_concurrency: 1
  partial:
    method: GET
    path: /health
    warmup: 0
    iterations: 1
    client_concurrency: 1
    implementations: [python, rust]
  cold:
    mode: cold-start
    method: GET
    path: /health
    warmup: 0
    iterations: 20
    client_concurrency: 1
"""

# 実際のCLIの代わりに、工程名を出力して指定どおりに成功・失敗・待機する偽のコマンド。
FAKE_CLI = f"""
import sys, time
step = sys.argv[1]
print(f"{{step}} via {SECRET_URL}", flush=True)
if step == "{{fail}}":
    sys.exit(4)
if step == "{{slow}}":
    time.sleep(30)
"""


IMPLEMENTATIONS = (
    Implementation("python", "Python", "apps/python"),
    Implementation("rust", "Rust", "apps/rust"),
    Implementation("java", "Java", "apps/java"),
)


def _settings(**overrides):
    values = {
        "implementations": IMPLEMENTATIONS,
        "endpoints": {item.key: SECRET_URL for item in IMPLEMENTATIONS},
        "architecture": "arm64",
        "memory_mb": 512,
        "region": "ap-northeast-1",
        "log_groups": {item.key: f"/aws/lambda/{item.key}" for item in IMPLEMENTATIONS},
        **overrides,
    }
    return LaunchSettings(**values)


def _manager(tmp_path, *, fail="", slow="", settings=None):
    workload_file = tmp_path / "workloads.yaml"
    workload_file.write_text(WORKLOADS, encoding="utf-8")
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI.replace("{fail}", fail).replace("{slow}", slow), encoding="utf-8")
    resolved = settings or _settings()
    return JobManager(
        settings_provider=lambda: resolved,
        workload_file=workload_file,
        results_dir=tmp_path / "results",
        pricing_path=tmp_path / "pricing.yaml",
        log_wait_seconds=0,
        command_prefix=[sys.executable, str(script)],
    )


def _request(**overrides):
    return {
        "workload": "health",
        "iterations": 5,
        "client_concurrency": 1,
        "warmup": 2,
        **overrides,
    }


def _wait_until_finished(manager, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.current()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("計測が時間内に終わりませんでした")


def _wait_for_step(manager, step, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.current()["step"] == step and manager.current()["output"]:
            return
        time.sleep(0.05)
    raise AssertionError(f"{step} 工程に到達しませんでした")


def test_start_runs_all_steps_and_masks_api_urls(tmp_path):
    manager = _manager(tmp_path)

    started = manager.start(_request())
    job = _wait_until_finished(manager)

    # 言語を選ばなければ一覧の全言語（3つ）へ、ウォームアップ込みで7回ずつ送る。
    assert started["targets"] == ["python", "rust", "java"]
    assert started["total_requests"] == 21
    assert job["state"] == "succeeded"
    assert [line.split()[0] for line in job["output"]] == ["run", "collect", "report"]
    assert SECRET_URL not in json.dumps(job)
    assert "<API URL>" in job["output"][0]


def test_selected_languages_are_measured_in_registry_order(tmp_path):
    manager = _manager(tmp_path)

    started = manager.start(_request(targets=["java", "python"]))
    _wait_until_finished(manager)

    assert started["targets"] == ["python", "java"]
    assert started["total_requests"] == 14
    commands = manager._commands(manager._job)
    assert commands["run"][commands["run"].index("--target") + 1] == "python,java"
    assert "rust=/aws/lambda/rust" not in " ".join(commands["collect"])
    assert "java=/aws/lambda/java" in commands["collect"]


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        (["python"], "二つ以上"),
        (["python", "cobol"], "cobol"),
        ("python,rust", "指定が正しくありません"),
    ],
)
def test_invalid_language_selection_is_rejected(tmp_path, targets, message):
    manager = _manager(tmp_path)

    with pytest.raises(LaunchError, match=message):
        manager.start(_request(targets=targets))


def test_workload_limited_to_some_languages_measures_only_those(tmp_path):
    manager = _manager(tmp_path)

    started = manager.start(_request(workload="partial", iterations=1, warmup=0))
    _wait_until_finished(manager)

    assert started["targets"] == ["python", "rust"]
    with pytest.raises(LaunchError, match="java"):
        manager.start(_request(workload="partial", targets=["python", "java"]))


def test_failed_step_stops_pipeline_and_reports_step(tmp_path):
    manager = _manager(tmp_path, fail="collect")

    manager.start(_request())
    job = _wait_until_finished(manager)

    assert job["state"] == "failed"
    assert job["step"] == "collect"
    assert "ログ収集（反映を待ちながら）に失敗しました" in job["message"]
    assert not any(line.startswith("report") for line in job["output"])


def test_second_start_is_rejected_while_running_and_cancel_stops_it(tmp_path):
    manager = _manager(tmp_path, slow="run")
    manager.start(_request())
    _wait_for_step(manager, "run")

    with pytest.raises(JobConflict):
        manager.start(_request())

    manager.cancel()
    job = _wait_until_finished(manager)
    assert job["state"] == "cancelled"
    assert job["step"] == "run"


@pytest.mark.parametrize(
    ("request_overrides", "message"),
    [
        ({"workload": "unknown"}, "ワークロード"),
        ({"iterations": 0}, "iterations"),
        ({"iterations": 2001}, "iterations"),
        ({"client_concurrency": True}, "client_concurrency"),
        ({"warmup": "1"}, "warmup"),
    ],
)
def test_start_rejects_invalid_requests(tmp_path, request_overrides, message):
    manager = _manager(tmp_path)

    with pytest.raises(LaunchError, match=message):
        manager.start(_request(**request_overrides))
    assert manager.current() is None


def test_start_rejects_when_settings_are_missing(tmp_path):
    manager = _manager(tmp_path, settings=_settings(architecture=None))

    with pytest.raises(LaunchError, match="アーキテクチャ"):
        manager.start(_request())


@pytest.fixture
def server(tmp_path):
    manager = _manager(tmp_path)
    server = create_server(
        port=0,
        results_dir=tmp_path / "results",
        pricing_path=tmp_path / "pricing.yaml",
        jobs=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _call(server, method, path, *, body=None, headers=None):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, payload


def test_launch_options_hide_urls_and_list_workloads(server):
    status, payload = _call(server, "GET", "/api/launch")

    assert status == 200
    assert payload["settings"]["ready"] is True
    assert payload["workloads"][0]["name"] == "health"
    assert payload["workloads"][0]["cost_basis"] is None
    assert SECRET_URL not in json.dumps(payload)


def test_post_starts_job_with_json_from_same_origin(server):
    port = server.server_address[1]
    status, payload = _call(
        server,
        "POST",
        "/api/jobs",
        body=json.dumps(_request()),
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
    )

    assert status == 202
    assert payload["job"]["state"] == "running"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/json", "Origin": "https://evil.example"}, 403),
        ({"Content-Type": "application/json", "Host": "evil.example:8765"}, 403),
    ],
)
def test_post_rejects_cross_site_or_non_json_requests(server, headers, expected):
    status, payload = _call(
        server, "POST", "/api/jobs", body=json.dumps(_request()), headers=headers
    )

    assert status == expected
    assert "error" in payload


@pytest.mark.parametrize("origin", ["http://127.0.0.1:1", "http://localhost:{port}", "null"])
def test_post_rejects_origins_that_differ_from_the_page_even_on_this_machine(server, origin):
    port = server.server_address[1]
    # 同じマシンの別のポートや別の名前で開いたページも、画面自身ではないため拒否する。
    status, payload = _call(
        server,
        "POST",
        "/api/jobs",
        body=json.dumps(_request()),
        headers={"Content-Type": "application/json", "Origin": origin.format(port=port)},
    )

    assert status == 403
    assert "error" in payload


def test_post_returns_400_for_invalid_input(server):
    status, payload = _call(
        server,
        "POST",
        "/api/jobs",
        body=json.dumps(_request(iterations=0)),
        headers={"Content-Type": "application/json"},
    )

    assert status == 400
    assert "iterations" in payload["error"]


def test_missing_command_finishes_as_failed_and_allows_next_job(tmp_path):
    manager = _manager(tmp_path)
    manager.command_prefix = [str(tmp_path / "no-such-command")]

    manager.start(_request())
    job = _wait_until_finished(manager)

    # 子プロセスを起動できなくても、実行中のまま残さず失敗で終える。
    assert job["state"] == "failed"
    assert "想定外のエラー" in job["message"]
    assert "FileNotFoundError" in job["message"]
    manager.command_prefix = [sys.executable, str(tmp_path / "fake_cli.py")]
    manager.start(_request())
    assert _wait_until_finished(manager)["state"] == "succeeded"


def test_command_build_failure_finishes_as_failed(tmp_path):
    # ロググループが欠けた設定でコマンドを組み立てると、工程の前に例外になる。
    manager = _manager(tmp_path)

    def broken_commands(job):
        raise KeyError("python")

    manager._commands = broken_commands
    manager.start(_request())

    job = _wait_until_finished(manager)
    assert job["state"] == "failed"
    assert "KeyError" in job["message"]


def test_incomplete_log_collection_still_reports_but_does_not_succeed(tmp_path):
    manager = _manager(tmp_path)
    script = tmp_path / "fake_cli.py"
    script.write_text(
        script.read_text(encoding="utf-8") + '\nif step == "collect":\n    sys.exit(3)\n',
        encoding="utf-8",
    )

    manager.start(_request())
    job = _wait_until_finished(manager)

    # 期限内にログがそろわなくてもレポートは作るが、完全な成功とは示さない。
    assert job["state"] == "failed"
    assert any(line.startswith("report") for line in job["output"])
    assert "make collect" in job["message"]


@pytest.mark.parametrize(
    ("length", "expected"),
    [("abc", 400), ("-1", 400), ("1e3", 400), ("5000", 413), ("0", 400)],
)
def test_post_rejects_invalid_content_length_before_reading(server, length, expected):
    # 長さ0は空のオブジェクトとして読み、必須の入力がないため入力エラー（400）になる。
    status, payload = _call(
        server,
        "POST",
        "/api/estimate",
        headers={"Content-Type": "application/json", "Content-Length": length},
    )

    assert status == expected
    assert payload["error"]


@pytest.mark.parametrize("overrides", [{"warmup": 1}, {"client_concurrency": 2}])
def test_cold_start_launch_keeps_warmup_zero_and_single_concurrency(tmp_path, overrides):
    manager = _manager(tmp_path)

    # 計測前に実行環境を使う指定は、コールドスタートを測れないため開始しない。
    with pytest.raises(LaunchError, match="コールドスタート"):
        manager.start(_request(workload="cold", **{"iterations": 20, "warmup": 0, **overrides}))
