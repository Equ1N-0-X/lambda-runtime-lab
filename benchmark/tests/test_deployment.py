"""デプロイ済みイメージのタグから版を読み取り、手元のアプリとのずれを検出できることを検証する。"""

from botocore.exceptions import ClientError

from benchmark_runner.deployment import DeployedImage, check_deployed_revision


def _check(tags, changed=False, read_error=None):
    def read_image(function_name, region):
        if read_error:
            raise read_error
        return DeployedImage(repository="lab-rust", digest="sha256:abc", tags=tuple(tags))

    return check_deployed_revision(
        label="Rust",
        function_name="lab-rust",
        app_dir="apps/rust",
        region="ap-northeast-1",
        read_image=read_image,
        changed_since=lambda revision, app_dir: changed,
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
