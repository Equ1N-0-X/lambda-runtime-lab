"""デプロイ済みLambdaのイメージが、手元のアプリの最新コミットから作られたものかを確かめる。

計測処理はアプリが出力するログ（計測番号など）を前提にしている。古い版のまま計測すると
Lambda側の指標を結び付けられないデータになるため、計測前に版のずれを検出する。

イメージのタグには、公開時のGitコミット（-g<sha>、未コミットの変更があれば -dirty）を含める
（scripts/publish-images.sh）。デプロイ済みイメージのタグからそのコミットを読み、
そのコミット以降にイメージの元になるファイルが変更されていないかをGitで確認する。
イメージに入らないテスト（spec/implementations.yaml の test_paths）の変更は、ずれとみなさない。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
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


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False
    )


class RevisionCheckError(RuntimeError):
    """Gitの異常終了などで、版のずれを判定できなかったことを表す。"""


def sources_changed_since(
    revision: str, source_pathspecs: Sequence[str], *, root: Path = ROOT
) -> bool | None:
    """指定コミット以降にイメージの元になるファイルが変わったかを返す。

    手元の履歴にないコミットならNoneを返す。Gitが異常終了して判定できなければ
    RevisionCheckErrorを送出し、変更なしと誤って判定しない。
    """
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    # 手元のHEADを読めなければ、Gitが使えないかリポジトリが壊れており比較の基準がない。
    if head.returncode != 0:
        raise RevisionCheckError(_git_failure("rev-parse", head))
    # HEADを読めるのにコミットが見つからなければ、手元の履歴にない版である（存在しない場合も128）。
    if _git(root, "cat-file", "-e", f"{revision}^{{commit}}").returncode != 0:
        return None
    diff = _git(root, "diff", "--quiet", revision, "HEAD", "--", *source_pathspecs)
    # git diff --quiet は差分がなければ0、あれば1を返す。それ以外（128など）は判定不能とする。
    if diff.returncode in (0, 1):
        return diff.returncode == 1
    raise RevisionCheckError(_git_failure("diff", diff))


def _git_failure(command: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or "").strip().splitlines()
    return f"git {command} が終了コード {result.returncode} で失敗しました" + (
        f"（{detail[0]}）" if detail else ""
    )


def check_deployed_revision(
    *,
    label: str,
    function_name: str,
    source_pathspecs: Sequence[str],
    region: str,
    read_image: Callable[[str, str], DeployedImage] = read_deployed_image,
    changed_since: Callable[[str, Sequence[str]], bool | None] = sources_changed_since,
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
    try:
        changed = changed_since(match["sha"], source_pathspecs)
    # Gitで判定できなければ、版が一致するか分からないまま計測させない。
    except RevisionCheckError as exc:
        return f"{label} のデプロイ済みの版を照合できませんでした（{exc}）。"
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
