"""CLIからのAWS計測が画面と同じ事前確認を通り、確かめた実体を記録することを検証する。"""

import json

import pytest

from benchmark_runner import cli
from benchmark_runner.dashboard.settings import LaunchSettings, Problem
from benchmark_runner.implementations import load_implementations

IMPLEMENTATIONS = load_implementations()
ENDPOINTS = {item.key: f"https://{item.key}.example" for item in IMPLEMENTATIONS}


def _settings(*, problems=(), architecture="arm64", memory_mb=512):
    return LaunchSettings(
        implementations=IMPLEMENTATIONS,
        endpoints=dict(ENDPOINTS),
        log_groups={item.key: f"/aws/lambda/{item.key}" for item in IMPLEMENTATIONS},
        architecture=architecture,
        memory_mb=memory_mb,
        region="ap-northeast-1",
        problems=tuple(Problem(summary=problem) for problem in problems),
        deployments={
            item.key: {"function_name": f"lab-{item.key}", "image_digest": f"sha256:{item.key}"}
            for item in IMPLEMENTATIONS
        },
    )


@pytest.fixture
def resolved(monkeypatch):
    holder = {"settings": _settings(), "calls": 0}

    class Resolver:
        def __init__(self, **kwargs):
            holder["environ"] = kwargs["environ"]

        def resolve(self):
            holder["calls"] += 1
            return holder["settings"]

    monkeypatch.setattr(cli, "SettingsResolver", Resolver)
    monkeypatch.setattr(cli, "_terraform_output", lambda directory, name: dict(ENDPOINTS))
    # 実際の送信は行わず、計測直前の設定（RunConfig）を確かめる。
    captured = {}

    async def fake_run(config, workload):
        captured["config"] = config
        return {"outcomes": {}}

    monkeypatch.setattr(cli, "run_benchmark", fake_run)
    holder["captured"] = captured
    return holder


def _run(*arguments):
    return cli.main(["run", "--workload", "health", "--run-id", "verify", *arguments])


def test_signed_run_records_verified_conditions_and_deployments(resolved, capsys):
    assert _run("--architecture", "arm64", "--memory-mb", "512", "--region", "us-west-2") == 0

    config = resolved["captured"]["config"]
    assert (config.architecture, config.memory_mb) == ("arm64", 512)
    assert config.evidence["verification"]["mode"] == "aws"
    assert config.evidence["verification"]["deployments"]["rust"]["image_digest"] == "sha256:rust"
    assert config.evidence["content_ids"]["pricing"].startswith("sha256:")
    assert resolved["environ"]["AWS_REGION"] == "us-west-2"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--architecture", "x86_64"), "--architecture x86_64"),
        (("--memory-mb", "1024"), "--memory-mb 1024"),
        (("--endpoint", "rust=https://other.example"), "rust"),
    ],
)
def test_signed_run_rejects_conditions_that_differ_from_lambda(
    resolved, capsys, arguments, message
):
    assert _run(*arguments) == 2

    assert message in capsys.readouterr().err
    assert "config" not in resolved["captured"]


def test_signed_run_stops_when_launch_check_has_problems(resolved, capsys):
    resolved["settings"] = _settings(problems=["Rust のアプリはデプロイ後に変更されています。"])

    assert _run() == 2

    assert "デプロイ後に変更" in capsys.readouterr().err


def test_unsigned_local_run_skips_aws_check_and_is_marked_local(resolved, monkeypatch):
    monkeypatch.setenv("PYTHON_API_ENDPOINT", "http://127.0.0.1:8000")

    assert _run("--unsigned", "--target", "python") == 0

    assert resolved["calls"] == 0
    assert resolved["captured"]["config"].evidence["verification"] == {"mode": "local"}


def test_dry_run_plan_contains_request_identity(capsys):
    assert cli.main(["run", "--workload", "health", "--dry-run"]) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["experiment_id"].startswith("sha256:")
    assert plan["verification"] == {"mode": "local"}


def test_cold_start_run_updates_the_verified_lambda_functions(resolved):
    assert cli.main(["run", "--workload", "cold-start", "--run-id", "verify"]) == 0

    config = resolved["captured"]["config"]
    # 設定を更新する関数は、計測前に確かめたデプロイ済みの関数と同じにする。
    assert config.function_names["rust"] == "lab-rust"
    assert config.cold_starter is not None


def test_cold_start_run_is_rejected_for_unsigned_local_apis(resolved, capsys):
    assert cli.main(["run", "--workload", "cold-start", "--unsigned"]) == 2

    assert "AWS 上の Lambda に対してだけ" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["collect", "report"])
def test_collect_and_report_reject_run_ids_that_escape_the_results_directory(
    command, tmp_path, capsys
):
    results = tmp_path / "results"
    # 親ディレクトリを指す値や、ログ検索の条件を壊す引用符を含む値は、読み書きの前に拒否する。
    for run_id in ("../outside", 'x" or @message like "'):
        arguments = [command, "--run-id", run_id, "--results-dir", str(results)]
        assert cli.main(arguments) == 2
    assert "run ID" in capsys.readouterr().err
    assert not results.exists()
