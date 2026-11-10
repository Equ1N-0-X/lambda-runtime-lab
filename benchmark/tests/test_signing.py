"""長時間の計測で認証情報が更新され、署名に失敗した場合は中断として扱うことを検証する。"""

import asyncio
import datetime
import json

import httpx
import pytest
from botocore.credentials import ReadOnlyCredentials, RefreshableCredentials

from benchmark_runner import cli, runner
from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.sigv4 import SigningError, snapshot
from benchmark_runner.workloads import Workload


class FakeClock:
    def __init__(self):
        self.now = datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC)

    def __call__(self):
        return self.now


def _refreshable(clock):
    generation = {"value": 0}

    def refresh():
        generation["value"] += 1
        number = generation["value"]
        # 3項目は同じ世代の組として発行される。
        return {
            "access_key": f"AKIA{number}",
            "secret_key": f"secret-{number}",
            "token": f"token-{number}",
            "expiry_time": (clock() + datetime.timedelta(minutes=60)).isoformat(),
        }

    initial = refresh()
    return RefreshableCredentials(
        access_key=initial["access_key"],
        secret_key=initial["secret_key"],
        token=initial["token"],
        expiry_time=clock() + datetime.timedelta(minutes=60),
        refresh_using=refresh,
        method="test",
        time_fetcher=clock,
    )


def test_snapshot_follows_refresh_with_a_consistent_triple():
    clock = FakeClock()
    source = _refreshable(clock)

    first = snapshot(source)
    clock.now += datetime.timedelta(minutes=70)
    second = snapshot(source)

    assert first.access_key == "AKIA1"
    # 期限を過ぎた後は、更新された同じ世代のキー・シークレット・トークンで署名する。
    assert (second.access_key, second.secret_key, second.token) == (
        "AKIA2",
        "secret-2",
        "token-2",
    )


def test_refresh_failure_becomes_signing_error():
    class Broken:
        def get_frozen_credentials(self):
            raise RuntimeError("SSO session expired")

    with pytest.raises(SigningError, match="SSO session expired"):
        snapshot(Broken())


def test_signing_failure_mid_run_saves_partial_run_as_aborted(tmp_path, monkeypatch):
    calls = {"count": 0}

    class Source:
        def get_frozen_credentials(self):
            calls["count"] += 1
            # 二つ目の言語の途中で認証情報を更新できなくなる。
            if calls["count"] > 3:
                raise RuntimeError("expired")
            return ReadOnlyCredentials("AKIA", "secret", "token")

    monkeypatch.setattr(runner, "credential_source", lambda session: Source())
    config = RunConfig(
        run_id="signing",
        targets=("python", "rust"),
        endpoints={"python": "https://python.example", "rust": "https://rust.example"},
        region="ap-northeast-1",
        results_dir=tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    )

    with pytest.raises(SigningError):
        asyncio.run(run_benchmark(config, Workload("health", "GET", "/health", 0, 3, 1)))

    metadata = json.loads((tmp_path / "raw/signing/metadata.json").read_text())
    assert "署名に失敗" in metadata["aborted"]
    assert metadata["completed_at"] is not None
    assert metadata["sample_count"] == 3


def test_cli_turns_signing_error_into_actionable_message(monkeypatch, capsys):
    async def failing(config, workload):
        raise SigningError("AWS標準認証チェーンから認証情報を取得できませんでした")

    monkeypatch.setattr(cli, "run_benchmark", failing)

    code = cli.main(["run", "--workload", "health", "--unsigned"])

    error = capsys.readouterr().err
    assert code == 2
    assert "ログインし直して" in error
    assert "Traceback" not in error
