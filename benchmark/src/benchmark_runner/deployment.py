"""デプロイ済みLambdaのイメージが、手元のアプリの最新コミットから作られたものかを確かめる。

計測処理はアプリが出力するログ（計測番号など）を前提にしている。古い版のまま計測すると
Lambda側の指標を結び付けられないデータになるため、計測前に版のずれを検出する。

イメージのタグには、公開時のGitコミット（-g<sha>、未コミットの変更があれば -dirty）を含める
（scripts/publish-images.sh）。デプロイ済みイメージのタグからそのコミットを読み、
そのコミット以降にアプリのディレクトリが変更されていないかをGitで確認する。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[3]

TAG_REVISION_PATTERN = re.compile(r"-g(?P<sha>[0-9a-f]{7,40})(?P<dirty>-dirty)?$")
REPUBLISH_HINT = (
    "make publish-images と make deploy で最新のアプリをデプロイしてから計測してください。"
)


@dataclass(frozen=True)
class DeployedImage:
    """デプロイ済みLambdaが参照しているイメージの識別情報。"""

    repository: str
    digest: str
    tags: tuple[str, ...]


def read_deployed_image(function_name: str, region: str) -> DeployedImage:
    """Lambda関数の実イメージのdigestと、ECR上でそのdigestに付いたタグを読む。"""
    lambda_client = boto3.client("lambda", region_name=region)
    function = lambda_client.get_function(FunctionName=function_name)
    code = function.get("Code", {})
    # タグ指定でデプロイしても実体は解決済みdigestで動くため、解決済みの方を優先する。
    image_uri = code.get("ResolvedImageUri") or code.get("ImageUri") or ""
    repository_uri, _, digest = image_uri.partition("@")
    repository = repository_uri.split("/", 1)[1] if "/" in repository_uri else repository_uri
    repository = repository.split(":", 1)[0]

    ecr_client = boto3.client("ecr", region_name=region)
    described = ecr_client.describe_images(
        repositoryName=repository, imageIds=[{"imageDigest": digest}]
    )
    details = described.get("imageDetails") or [{}]
    return DeployedImage(
        repository=repository, digest=digest, tags=tuple(details[0].get("imageTags") or ())
    )


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments], capture_output=True, text=True, check=False
    )


def app_changed_since(revision: str, app_dir: str) -> bool | None:
    """指定コミット以降にアプリのディレクトリが変わったかを返す。判定できなければNone。"""
    # 手元の履歴にないコミットなら、比較の基準がないため判定できない。
    if _git("cat-file", "-e", f"{revision}^{{commit}}").returncode != 0:
        return None
    # git diff --quiet は差分があると1、なければ0を返す。
    return _git("diff", "--quiet", revision, "HEAD", "--", app_dir).returncode == 1


def check_deployed_revision(
    *,
    label: str,
    function_name: str,
    app_dir: str,
    region: str,
    read_image: Callable[[str, str], DeployedImage] = read_deployed_image,
    changed_since: Callable[[str, str], bool | None] = app_changed_since,
) -> str | None:
    """デプロイ済みの版が手元のアプリと一致しなければ、その理由と対処を返す。一致すればNone。"""
    try:
        image = read_image(function_name, region)
    # 権限不足や関数の未作成などで読めなければ、版を確認できないまま計測させない。
    except (ClientError, BotoCoreError) as exc:
        return f"{label} のデプロイ済みイメージを確認できませんでした（{exc}）。"

    revisions = [
        match for tag in image.tags if (match := TAG_REVISION_PATTERN.search(tag)) is not None
    ]
    # コミット付きのタグがなければ、どのコードから作ったイメージか分からない。
    if not revisions:
        return (
            f"{label} のデプロイ済みイメージがどのコミットから作られたか分かりません。"
            + REPUBLISH_HINT
        )
    match = revisions[0]
    # 未コミットの変更を含めて作ったイメージは、どのコードと一致するか確かめられない。
    if match["dirty"]:
        return (
            f"{label} のデプロイ済みイメージは、未コミットの変更を含めて作られています。"
            "変更をコミットしてから" + REPUBLISH_HINT
        )
    changed = changed_since(match["sha"], app_dir)
    # 手元の履歴にないコミットから作られていれば、別の作業ブランチの版の可能性がある。
    if changed is None:
        return (
            f"{label} のデプロイ済みイメージのコミット {match['sha']} が手元の履歴にありません。"
            + REPUBLISH_HINT
        )
    # デプロイ後にアプリが変更されていれば、計測処理の前提と食い違う可能性がある。
    if changed:
        return (
            f"{label} のアプリはデプロイ後（{match['sha'][:7]}）に変更されています。"
            + REPUBLISH_HINT
        )
    return None
