"""AWSを操作するworkflowが、安全な状態管理が確認されるまでAWS操作へ進まないことを検証する。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_aws_benchmark_workflow_is_suspended_before_any_aws_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/benchmark.yml").read_text())
    jobs = workflow["jobs"]

    suspended = jobs["suspended"]
    commands = "\n".join(step.get("run", "") for step in suspended["steps"])
    # 停止用のジョブはAWSの認証もTerraformも使わず、必ず失敗する。
    assert "exit 1" in commands
    assert not any("uses" in step for step in suspended["steps"])
    assert "make" not in commands and "terraform" not in commands
    # デプロイ・削除を含むジョブは停止用のジョブに依存し、always() の工程も含めて動かない。
    assert jobs["benchmark"]["needs"] == "suspended"
    assert set(jobs) == {"suspended", "benchmark"}
