"""イメージ公開スクリプトのドライラン、安全な引数検証、digest出力を検証する。"""

import os
import subprocess
from pathlib import Path

import pytest

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
    assert "-target=aws_ecr_lifecycle_policy.runtime" in output
    assert "aws_lambda_function" not in output


def test_lifecycle_rule_matches_the_tags_that_publish_images_creates():
    import re

    publish = (ROOT / "scripts/publish-images.sh").read_text(encoding="utf-8")
    ecr = (ROOT / "infrastructure/terraform/ecr.tf").read_text(encoding="utf-8")
    prefix = re.search(r'TAG="\$\{TAG:-([A-Za-z0-9_.-]+?)\$\(date', publish).group(1)

    # 自動削除の対象は、make publish-images が付ける既定のタグだけにする。
    # 名前がずれると古いイメージが消えない、または手で付けたタグまで消える。
    assert prefix == "benchmark-"
    assert f'tagPrefixList = ["{prefix}"]' in ecr


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


def _with_fake_uv(tmp_path, body):
    """実装一覧を返すuvを偽コマンドに差し替え、一覧取得の失敗を再現する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "uv"
    fake.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    fake.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.mark.parametrize(
    ("body", "extra_env", "message"),
    [
        ("echo 'broken registry' >&2; exit 7", {}, "終了コード 7"),
        ("exit 0", {}, "実装がありません"),
        ("printf 'python\\nrust\\n'", {"IMPLEMENTATIONS": "python cobol"}, "cobol"),
    ],
)
@pytest.mark.parametrize("script", ["build-images.sh", "publish-images.sh"])
def test_unreadable_implementation_list_stops_before_building(
    tmp_path, body, extra_env, message, script
):
    env = _with_fake_uv(tmp_path, body) | {
        "TAG": "benchmark-test",
        "PYTHON_ECR_REPOSITORY_URL": PYTHON_REPOSITORY,
        "RUST_ECR_REPOSITORY_URL": RUST_REPOSITORY,
        **extra_env,
    }
    result = subprocess.run(
        ["bash", f"scripts/{script}", "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    # イメージを一つも作らないまま成功（終了コード0）にしない。
    assert result.returncode != 0
    assert "docker buildx build" not in result.stdout
    assert message in result.stderr


def test_implementations_filter_accepts_known_keys(tmp_path):
    env = _with_fake_uv(
        tmp_path,
        'case "$*" in *keys*) printf "python\\nrust\\n";; '
        '*tsv*) printf "python\\tapps/python\\nrust\\tapps/rust\\n";; esac',
    ) | {"IMPLEMENTATIONS": "rust", "FINAL_BASE": "base"}
    result = subprocess.run(
        ["bash", "scripts/build-images.sh", "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )

    assert result.stdout.count("docker buildx build") == 1
    assert "apps/rust" in result.stdout


def _push_with_stubs(tmp_path, repositories, *, fail_login=False):
    """awsとdockerを偽コマンドにして、AWS・Dockerへ触れずに認証の回数を数える。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    (bin_dir / "aws").write_text(
        f'#!/usr/bin/env bash\necho "aws $*" >> "{log}"\n'
        + ("exit 1\n" if fail_login else "echo token\n")
    )
    (bin_dir / "docker").write_text(
        f'#!/usr/bin/env bash\ncat > /dev/null; echo "docker $*" >> "{log}"\n'
    )
    (bin_dir / "uv").write_text("#!/usr/bin/env bash\nprintf 'python\\nrust\\n'\n")
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TAG": "t",
        "PYTHON_ECR_REPOSITORY_URL": repositories[0],
        "RUST_ECR_REPOSITORY_URL": repositories[1],
    }
    result = subprocess.run(
        ["bash", "scripts/push-images.sh"], cwd=ROOT, env=env, capture_output=True, text=True
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_same_registry_is_logged_in_once(tmp_path):
    result, calls = _push_with_stubs(tmp_path, [PYTHON_REPOSITORY, RUST_REPOSITORY])

    assert result.returncode == 0, result.stderr
    assert sum(call.startswith("aws ecr get-login-password") for call in calls) == 1
    assert sum(call.startswith("docker push") for call in calls) == 2


def test_each_registry_is_logged_in_separately(tmp_path):
    other = "210987654321.dkr.ecr.ap-northeast-1.amazonaws.com/test-rust"
    _, calls = _push_with_stubs(tmp_path, [PYTHON_REPOSITORY, other])

    logins = [call for call in calls if call.startswith("docker login")]
    assert len(logins) == 2
    assert "210987654321" in logins[1]


def test_login_failure_stops_before_pushing(tmp_path):
    result, calls = _push_with_stubs(
        tmp_path, [PYTHON_REPOSITORY, RUST_REPOSITORY], fail_login=True
    )

    assert result.returncode != 0
    assert not any(call.startswith("docker push") for call in calls)
