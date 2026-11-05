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


def test_dry_run_applies_the_recorded_architecture():
    result = subprocess.run(
        ["bash", "scripts/benchmark-matrix.sh", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "MEMORIES": "256", "WORKLOADS": "health", "ARCHITECTURE": "arm64"},
        capture_output=True,
        check=True,
        text=True,
    )

    # 記録する方式と同じ値をTerraformへ渡し、実行IDも同じ方式から作る。
    assert "-var=architecture=arm64" in result.stdout
    assert "--run-id arm64-256-health-" in result.stdout
    assert "--architecture arm64" in result.stdout


def test_unverified_architecture_is_rejected_before_apply():
    result = subprocess.run(
        ["bash", "scripts/benchmark-matrix.sh", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "MEMORIES": "256", "WORKLOADS": "health", "ARCHITECTURE": "x86_64"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "terraform" not in result.stdout
    assert "arm64 だけ" in result.stderr


def test_rust_image_label_follows_the_build_target():
    dockerfile = (ROOT / "apps/rust/Dockerfile").read_text(encoding="utf-8")

    # 方式のラベルはBuildKitのTARGETARCHから付け、固定値で実体と食い違わせない。
    assert "target-architecture=$TARGETARCH" in dockerfile
    assert 'TARGET_ARCH="arm64"' not in dockerfile


def _run_with_stubs(tmp_path, *, fail_on=None, expected=None):
    """terraformとuvを偽コマンドに差し替え、AWSへ触れずに終了時の案内を確かめる。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "terraform").write_text(
        f'#!/usr/bin/env bash\n[[ "$*" == *"lambda_memory_mb={fail_on}"* ]] && exit 1\nexit 0\n'
    )
    (bin_dir / "uv").write_text("#!/usr/bin/env bash\nexit 0\n")
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MEMORIES": "512 1024",
        "WORKLOADS": "health",
        "LOG_PROPAGATION_WAIT_SECONDS": "0",
    }
    env.pop("LAMBDA_MEMORY_MB", None)
    if expected:
        env["LAMBDA_MEMORY_MB"] = expected
    return subprocess.run(
        ["bash", "scripts/benchmark-matrix.sh"], cwd=ROOT, env=env, capture_output=True, text=True
    )


def test_finish_reports_remaining_memory_and_expected_mismatch(tmp_path):
    result = _run_with_stubs(tmp_path, expected="512")

    assert result.returncode == 0
    assert "最後に適用した 1024 MB のまま" in result.stderr
    assert "LAMBDA_MEMORY_MB=512 と異なる" in result.stderr
    assert "make deploy" in result.stderr


def test_finish_without_expected_memory_only_reports_state(tmp_path):
    result = _run_with_stubs(tmp_path)

    assert "1024 MB のまま" in result.stderr
    assert "と異なる" not in result.stderr


def test_failure_while_applying_reports_unknown_state(tmp_path):
    result = _run_with_stubs(tmp_path, fail_on="1024")

    assert result.returncode != 0
    assert "1024 MB の適用中に失敗" in result.stderr
