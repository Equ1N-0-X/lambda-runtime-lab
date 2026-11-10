"""profileの上書きが送信本文と記録の両方へ同じ値で反映されることを検証する。

レビューで報告された「concurrentと記録した計測がsequentialの本文で送られる」再現を、
現在のコードで回帰テストとして残す（過去の再現スクリプトは一時領域から失われている）。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from benchmark_runner.cli import main
from benchmark_runner.runner import RunConfig, run_benchmark
from benchmark_runner.workloads import WorkloadError, apply_profile, load_workload

WORKLOADS = Path(__file__).resolve().parents[2] / "spec/workloads.yaml"


def test_profile_override_changes_sent_body_and_recorded_profile(tmp_path):
    workload = apply_profile(load_workload(WORKLOADS, "aggregate-sequential"), "concurrent")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"count": 5, "total_value": 1, "checksum": "0" * 64})

    config = RunConfig(
        run_id="profile",
        targets=("python",),
        endpoints={"python": "https://example.test"},
        region="ap-northeast-1",
        results_dir=tmp_path,
        sign_requests=False,
        transport=httpx.MockTransport(handler),
    )
    metadata = asyncio.run(run_benchmark(config, workload))

    assert {body["profile"] for body in bodies} == {"concurrent"}
    assert metadata["workload"]["profile"] == "concurrent"
    assert metadata["workload"]["body"]["profile"] == "concurrent"
    samples = (tmp_path / "raw/profile/samples.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["profile"] for line in samples} == {"concurrent"}


def test_cli_dry_run_plan_reflects_profile_override(capsys):
    code = main(
        ["run", "--workload", "aggregate-sequential", "--profile", "concurrent", "--dry-run"]
    )

    plan = json.loads(capsys.readouterr().out)
    assert code == 0
    assert plan["workload"]["body"]["profile"] == "concurrent"
    assert plan["workload"]["profile"] == "concurrent"


@pytest.mark.parametrize(
    ("name", "profile", "message"),
    [
        ("aggregate-sequential", "parallel", "sequential または concurrent"),
        ("health", "concurrent", "profileがない"),
    ],
)
def test_unknown_or_unsendable_profile_is_rejected_before_measuring(name, profile, message):
    with pytest.raises(WorkloadError, match=message):
        apply_profile(load_workload(WORKLOADS, name), profile)


def test_definition_profile_must_match_body(tmp_path):
    path = tmp_path / "workloads.yaml"
    path.write_text(
        "version: 1\nworkloads:\n  w:\n    method: POST\n    path: /aggregate\n"
        "    iterations: 1\n    profile: concurrent\n"
        "    body: {ids: [a], profile: sequential}\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkloadError, match="一致しません"):
        load_workload(path, "w")
