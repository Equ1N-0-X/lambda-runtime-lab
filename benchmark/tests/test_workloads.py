"""YAMLワークロードの正常読込、上書き、外部payload、入力不正を検証する。"""

import json

import pytest

from benchmark_runner.workloads import WorkloadError, load_workload


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
