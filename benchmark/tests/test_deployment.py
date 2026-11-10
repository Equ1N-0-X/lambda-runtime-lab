"""デプロイ済みイメージのタグから版を読み取り、手元のアプリとのずれを検出できることを検証する。"""

import subprocess

from botocore.exceptions import ClientError

from benchmark_runner.deployment import (
    DeployedImage,
    check_deployed_revision,
    sources_changed_since,
)
from benchmark_runner.implementations import Implementation


def _check(tags, changed=False, read_error=None):
    def read_image(function_name, region):
        if read_error:
            raise read_error
        return DeployedImage(repository="lab-rust", digest="sha256:abc", tags=tuple(tags))

    return check_deployed_revision(
        label="Rust",
        function_name="lab-rust",
        source_pathspecs=("apps/rust",),
        region="ap-northeast-1",
        read_image=read_image,
        changed_since=lambda revision, pathspecs: changed,
    )


def test_image_built_from_current_app_passes():
    assert _check(["benchmark-20261020T000000Z-g0123456789ab"]) is None


def test_app_changed_after_deploy_blocks_measurement():
    problem = _check(["benchmark-20261020T000000Z-g0123456789ab"], changed=True)

    assert "デプロイ後（0123456）に変更されています" in problem
    assert "make publish-images" in problem


def test_image_without_revision_tag_cannot_be_verified():
    assert "どのコミットから作られたか分かりません" in _check(["benchmark-20261013T140534Z"])


def test_image_built_with_uncommitted_changes_cannot_be_verified():
    assert "未コミットの変更" in _check(["benchmark-20261020T000000Z-g0123456789ab-dirty"])


def test_revision_missing_from_local_history_is_reported():
    assert "手元の履歴にありません" in _check(["benchmark-x-g0123456789ab"], changed=None)


def test_unreadable_deployment_is_reported_with_cause():
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "GetFunction"
    )

    assert "確認できませんでした" in _check([], read_error=error)


def _git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_all(root, message):
    _git(root, "add", "-A")
    _git(
        root, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", message
    )
    return _git(root, "rev-parse", "HEAD")


def _repository_with_app(tmp_path):
    """イメージの元（src）とイメージに入らないテスト（tests）を持つアプリのリポジトリを作る。"""
    _git(tmp_path, "init", "-q")
    (tmp_path / "apps/lab/src").mkdir(parents=True)
    (tmp_path / "apps/lab/tests").mkdir(parents=True)
    (tmp_path / "apps/lab/src/app.txt").write_text("v1")
    (tmp_path / "apps/lab/tests/test.txt").write_text("v1")
    return _commit_all(tmp_path, "deployed")


LAB = Implementation(key="lab", label="Lab", app_dir="apps/lab", test_paths=("tests",))


def test_test_only_change_after_deploy_is_not_a_revision_gap(tmp_path):
    deployed = _repository_with_app(tmp_path)
    (tmp_path / "apps/lab/tests/test.txt").write_text("v2")
    _commit_all(tmp_path, "fix test")

    # テストはイメージに入らないため、再デプロイを求めずに計測できる。
    assert sources_changed_since(deployed, LAB.image_source_pathspecs, root=tmp_path) is False


def test_source_change_after_deploy_is_a_revision_gap(tmp_path):
    deployed = _repository_with_app(tmp_path)
    (tmp_path / "apps/lab/src/app.txt").write_text("v2")
    _commit_all(tmp_path, "change app")

    assert sources_changed_since(deployed, LAB.image_source_pathspecs, root=tmp_path) is True


def test_unknown_revision_cannot_be_compared(tmp_path):
    _repository_with_app(tmp_path)

    assert sources_changed_since("0123456789ab", LAB.image_source_pathspecs, root=tmp_path) is None
