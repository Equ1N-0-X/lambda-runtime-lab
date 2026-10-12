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
