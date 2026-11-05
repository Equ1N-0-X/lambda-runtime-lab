"""CLIからのAWS計測が画面と同じ事前確認を通り、確かめた実体を記録することを検証する。"""

import json

import pytest

from benchmark_runner import cli
from benchmark_runner.dashboard.settings import LaunchSettings
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
        problems=tuple(problems),
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
    assert plan["request_id"].startswith("sha256:")
    assert plan["verification"] == {"mode": "local"}
