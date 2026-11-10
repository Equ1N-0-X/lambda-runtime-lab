"""マトリクス実行のドライランが全メモリ・ワークロード条件を展開することを検証する。"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_uses_benchmark_project_environment():
    result = subprocess.run(
        ["bash", "scripts/benchmark-matrix.sh", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "MEMORIES": "256", "WORKLOADS": "health"},
        capture_output=True,
        check=True,
        text=True,
    )

    assert "uv run --project" in result.stdout
    assert "benchmark_runner run" in result.stdout
    assert "python3 -m benchmark_runner" not in result.stdout


def test_dry_run_applies_memory_to_the_deployed_environment_only():
    env = {key: value for key, value in os.environ.items() if key != "ENVIRONMENT"}
    result = subprocess.run(
        ["bash", "scripts/benchmark-matrix.sh", "--dry-run"],
        cwd=ROOT,
        env={**env, "MEMORIES": "256", "WORKLOADS": "health"},
        capture_output=True,
        check=True,
        text=True,
    )

    # 環境名を渡さないとTerraformの既定（dev）で全リソースを作り直すため、デプロイ時と同じ値を渡す。
    assert "-var=environment=benchmark" in result.stdout
    assert "-var=aws_region=" in result.stdout
    assert "-var=lambda_memory_mb=256" in result.stdout
