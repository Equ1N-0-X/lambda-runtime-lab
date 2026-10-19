"""イメージ公開スクリプトのドライラン、安全な引数検証、digest出力を検証する。"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_REPOSITORY = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/test-python"
RUST_REPOSITORY = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/test-rust"


def run_dry_run(script: str) -> str:
    result = subprocess.run(
        ["bash", f"scripts/{script}", "--dry-run"],
        cwd=ROOT,
        env={
            **os.environ,
            "AWS_REGION": "ap-northeast-1",
            "ENVIRONMENT": "benchmark",
            "TAG": "benchmark-test",
            "PYTHON_ECR_REPOSITORY_URL": PYTHON_REPOSITORY,
            "RUST_ECR_REPOSITORY_URL": RUST_REPOSITORY,
        },
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def test_bootstrap_ecr_dry_run_targets_only_ecr_resources():
    output = run_dry_run("bootstrap-ecr.sh")

    assert "terraform" in output
    assert "-target=aws_ecr_repository.runtime" in output
    assert "-target=aws_ecr_repository_policy.lambda_pull" in output
    assert "aws_lambda_function" not in output


def test_publish_images_dry_run_builds_pushes_and_prints_digest_uris():
    output = run_dry_run("publish-images.sh")

    assert "docker buildx build" in output
    assert "docker push" in output
    # 実装一覧の全言語について、digest固定URIをTerraformの変数として書き出す。
    assert '"image_uris"' in output
    assert f'"python": "{PYTHON_REPOSITORY}@' in output
    assert f'"rust": "{RUST_REPOSITORY}@' in output
    assert "make deploy" in output


def test_publish_images_default_tag_records_source_commit():
    env = {key: value for key, value in os.environ.items() if key != "TAG"} | {
        "AWS_REGION": "ap-northeast-1",
        "PYTHON_ECR_REPOSITORY_URL": PYTHON_REPOSITORY,
        "RUST_ECR_REPOSITORY_URL": RUST_REPOSITORY,
    }
    result = subprocess.run(
        ["bash", "scripts/publish-images.sh", "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()

    # 計測前の版の確認がタグからコミットを読み取れるよう、-g<コミット> を含める。
    assert f"-g{revision}" in result.stdout
