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
