"""YAMLワークロードの正常読込、上書き、外部payload、入力不正を検証する。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from benchmark_runner.workloads import Workload, WorkloadError, load_workload

ROOT = Path(__file__).resolve().parents[2]


def test_loads_inline_and_file_payloads(tmp_path, monkeypatch):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"message": "hello"}), encoding="utf-8")
    config = tmp_path / "workloads.yaml"
    config.write_text(
        """
version: 1
workloads:
  inline:
    method: post
    path: /compute
    warmup: 0
    iterations: 5
    client_concurrency: 2
    body:
      profile: sequential
  file:
    method: POST
    path: /transform
    warmup: 1
    iterations: 2
    client_concurrency: 1
    payload_file: payload.json
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    inline = load_workload(config, "inline")
    file_payload = load_workload(config, "file")

    assert inline.method == "POST"
    assert inline.profile == "sequential"
    assert file_payload.body == {"message": "hello"}


@pytest.mark.parametrize(
    "fragment",
    [
        "version: 2\nworkloads: {}",
        "version: 1\nworkloads: {}",
        ("version: 1\nworkloads:\n  bad:\n    method: GET\n    path: bad\n    iterations: 1\n"),
    ],
)
def test_rejects_invalid_workload(tmp_path, fragment):
    path = tmp_path / "workloads.yaml"
    path.write_text(fragment, encoding="utf-8")
    with pytest.raises(WorkloadError):
        load_workload(path, "bad")


def test_supported_languages_default_to_all_and_can_be_limited(tmp_path):
    config = tmp_path / "workloads.yaml"
    config.write_text(
        """
version: 1
workloads:
  everyone:
    method: GET
    path: /health
    iterations: 1
  newer:
    description: 一部の言語だけで先に計測する処理
    method: GET
    path: /health
    iterations: 1
    implementations: [rust, python]
""",
        encoding="utf-8",
    )
    available = ("python", "rust", "typescript", "java")

    assert load_workload(config, "everyone").supported(available) == available
    newer = load_workload(config, "newer")
    # 対応言語は実装一覧の表示順で返す。
    assert newer.supported(available) == ("python", "rust")
    assert newer.description == "一部の言語だけで先に計測する処理"


def test_rejects_empty_supported_language_list(tmp_path):
    config = tmp_path / "workloads.yaml"
    config.write_text(
        "version: 1\nworkloads:\n  x:\n    method: GET\n    path: /\n"
        "    iterations: 1\n    implementations: []\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkloadError, match="implementations"):
        load_workload(config, "x")


def test_cold_start_workload_requires_no_warmup_and_single_concurrency(tmp_path):
    from benchmark_runner.workloads import check_cold_start

    path = tmp_path / "workloads.yaml"
    path.write_text(
        "version: 1\nworkloads:\n  cold:\n    mode: cold-start\n    method: GET\n"
        "    path: /health\n    warmup: 0\n    iterations: 20\n    client_concurrency: 1\n",
        encoding="utf-8",
    )
    workload = load_workload(path, "cold")
    assert workload.cold_start is True

    # ウォームアップや同時要求があると、計測前に新しい実行環境が使われてしまう。
    for overrides in ({"warmup": 1}, {"client_concurrency": 2}, {"iterations": 101}):
        with pytest.raises(WorkloadError):
            check_cold_start(replace(workload, **overrides))


def test_cold_start_is_a_separate_experiment_from_the_same_request():
    from benchmark_runner.workloads import experiment_id

    standard = Workload("health", "GET", "/health", 0, 20, 1)

    assert experiment_id(standard) != experiment_id(replace(standard, cold_start=True))


def test_repository_defines_a_cold_start_workload():
    workload = load_workload(ROOT / "spec/workloads.yaml", "cold-start")

    assert workload.cold_start is True
    assert workload.iterations == 20
