"""画面から計測するための接続先と比較条件を、TerraformとAWS上のLambdaの実設定から求める。

利用者が環境変数を手で用意しなくても、デプロイ済みの環境をそのまま計測できるようにする。
環境変数で接続先が指定されていれば、make benchmark と同じくそちらを優先する。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
    SSOError,
    TokenRetrievalError,
)

from benchmark_runner.deployment import (
    DeployedImage,
    check_deployed_revision,
    read_deployed_image,
)
from benchmark_runner.implementations import DEFAULT_REGISTRY, Implementation, load_implementations

DEFAULT_REGION = "ap-northeast-1"
# Terraformの出力が返らないまま画面の表示を待たせ続けないための上限。
TERRAFORM_TIMEOUT_SECONDS = 30


class SettingsUnavailable(Exception):
    """設定の取得元を読めない理由を、利用者が対処できる文言で表す。"""


@dataclass(frozen=True)
class FunctionConditions:
    """比較条件として記録するLambda関数の実設定。"""

    architecture: str
    memory_mb: int


@dataclass(frozen=True)
class LaunchSettings:
    """計測の接続先と、比較条件として記録するLambdaの実設定。

    実装ごとの値は、実装一覧の表示順に並べた実装キーの対応表で持つ。
    """

    implementations: tuple[Implementation, ...]
    endpoints: dict[str, str | None]
    log_groups: dict[str, str | None]
    architecture: str | None
    memory_mb: int | None
    region: str
    # 取得元を読めなかった理由や条件の不一致など、値の欠落だけでは表せない問題。
    problems: tuple[str, ...] = ()
    # 実装ごとに、計測先として確かめたLambda関数・実設定・イメージ。runの証跡として保存する。
    deployments: dict[str, dict[str, Any]] = field(default_factory=dict)

    def missing(self) -> list[str]:
        """計測を開始できない理由を列挙する。問題の説明があればそれを優先する。"""
        # 取得元の問題で値が欠けている場合、欠落項目の羅列より問題の説明の方が対処に役立つ。
        if self.problems:
            return list(self.problems)
        absent = []
        for implementation in self.implementations:
            # 接続先がない実装は計測できず、ロググループがない実装はLambda側の指標を得られない。
            if not self.endpoints.get(implementation.key):
                absent.append(f"{implementation.label} API の URL")
            if not self.log_groups.get(implementation.key):
                absent.append(f"{implementation.label} のロググループ")
        # アーキテクチャとメモリは比較条件とコスト計算の前提のため、欠ければ計測しない。
        if not self.architecture:
            absent.append("Lambda のアーキテクチャ")
        if not self.memory_mb:
            absent.append("Lambda のメモリ")
        return [f"{label}を取得できませんでした。" for label in absent]

    def public(self) -> dict[str, Any]:
        """画面に返してよい項目だけを返す。URLやロググループ名は含めない。"""
        missing = self.missing()
        return {
            "ready": not missing,
            "missing": missing,
            "implementations": [
                {"key": implementation.key, "label": implementation.label}
                for implementation in self.implementations
            ],
            "architecture": self.architecture,
            "memory_mb": self.memory_mb,
            "region": self.region,
        }

    def secrets(self) -> list[str]:
        """実行出力から伏せる接続先の文字列。"""
        return [value for value in self.endpoints.values() if value]


def read_terraform_outputs(terraform_dir: Path) -> dict[str, Any]:
    """Terraformの出力値を読み、名前から値への辞書として返す。"""
    try:
        result = subprocess.run(
            ["terraform", f"-chdir={terraform_dir}", "output", "-json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=TERRAFORM_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SettingsUnavailable(
            "terraform コマンドが見つかりません。Terraform をインストールしてください。"
        ) from exc
    # 出力がない、stateがないなどでTerraformが失敗した場合は、デプロイ前の可能性が高い。
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SettingsUnavailable(
            "Terraform の出力を読めません。"
            "AWS へのデプロイ（make deploy）が済んでいるか確認してください。"
        ) from exc
    outputs = json.loads(result.stdout or "{}")
    return {name: entry.get("value") for name, entry in outputs.items()}


def read_lambda_conditions(function_name: str, region: str) -> FunctionConditions:
    """AWS上のLambda関数から、アーキテクチャとメモリの実設定を読む。"""
    client = boto3.client("lambda", region_name=region)
    try:
        configuration = client.get_function_configuration(FunctionName=function_name)
    # 認証情報がない、またはSSOのセッションが切れている場合は、ログインし直せば解消する。
    except (NoCredentialsError, PartialCredentialsError, TokenRetrievalError, SSOError) as exc:
        raise SettingsUnavailable(
            "AWS の認証情報が見つからないか、期限が切れています。"
            "ターミナルで AWS にログインしてから「設定を再確認」を押してください。"
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        # 関数が存在しなければ、未デプロイか別のリージョン・環境名を参照している。
        if code == "ResourceNotFoundException":
            raise SettingsUnavailable(
                f"Lambda 関数 {function_name} が {region} に見つかりません。"
                "デプロイ済みか、リージョンが正しいか確認してください。"
            ) from exc
        # 読み取り権限がなければ、利用中のAWSプロファイルの権限を見直す必要がある。
        if code in ("AccessDeniedException", "UnrecognizedClientException"):
            raise SettingsUnavailable(
                "利用中の AWS 認証情報では Lambda の設定を読めません"
                "（lambda:GetFunctionConfiguration の権限が必要です）。"
            ) from exc
        raise SettingsUnavailable(f"Lambda の設定を読めませんでした（{code}）。") from exc
    except BotoCoreError as exc:
        raise SettingsUnavailable(f"AWS に接続できませんでした: {exc}") from exc
    return FunctionConditions(
        architecture=(configuration.get("Architectures") or ["x86_64"])[0],
        memory_mb=int(configuration["MemorySize"]),
    )


class SettingsResolver:
    """計測の都度、TerraformとLambdaから最新の設定を求める。

    デプロイ後に画面を起動し直さなくても、新しい設定で計測できるよう毎回読み直す。
    """

    def __init__(
        self,
        *,
        terraform_dir: Path,
        registry_path: Path = DEFAULT_REGISTRY,
        environ: Mapping[str, str] | None = None,
        read_outputs: Callable[[Path], dict[str, Any]] = read_terraform_outputs,
        read_conditions: Callable[[str, str], FunctionConditions] = read_lambda_conditions,
        check_revision: Callable[..., str | None] | None = check_deployed_revision,
        read_image: Callable[[str, str], DeployedImage] = read_deployed_image,
    ) -> None:
        self.terraform_dir = terraform_dir
        self.registry_path = registry_path
        self.environ = os.environ if environ is None else environ
        self.read_outputs = read_outputs
        self.read_conditions = read_conditions
        # Noneなら版の確認を省く（テストや、AWS以外の接続先を計測する場合）。
        self.check_revision = check_revision
        self.read_image = read_image

    def resolve(self) -> LaunchSettings:
        implementations = load_implementations(self.registry_path)
        region = self.environ.get("AWS_REGION") or DEFAULT_REGION
        problems: list[str] = []

        outputs: dict[str, Any] = {}
        try:
            outputs = self.read_outputs(self.terraform_dir)
        except SettingsUnavailable as exc:
            problems.append(str(exc))

        api_endpoints = outputs.get("api_endpoints") or {}
        lambda_names = outputs.get("lambda_names") or {}
        endpoints = {}
        for item in implementations:
            name, override = self._endpoint_override(item)
            deployed = api_endpoints.get(item.key)
            endpoints[item.key] = override or deployed
            # 上書きした接続先がTerraformで管理するAPIと違えば、呼び出す先と、設定・ログを読む
            # Lambdaが別の環境になり、記録が実体と食い違うため計測させない。
            if override and deployed and override != deployed:
                problems.append(
                    f"環境変数 {name} の接続先が、Terraform で管理する {item.label} の API と"
                    "異なります。呼び出す API と、設定・ログを読む Lambda の対応を"
                    "確かめられないため計測できません。"
                    f"意図した上書きでなければ unset {name} を実行してください。"
                )
        function_names = {item.key: lambda_names.get(item.key) for item in implementations}
        not_deployed = [item.label for item in implementations if not function_names[item.key]]
        # Terraformは読めたが一覧の実装がデプロイされていなければ、追加後のデプロイ漏れである。
        if outputs and not_deployed:
            problems.append(
                f"{'・'.join(not_deployed)} の Lambda がデプロイされていません。"
                "make bootstrap-ecr、make publish-images、make deploy を実行してください。"
            )

        conditions: dict[str, FunctionConditions] = {}
        for item in implementations:
            function_name = function_names[item.key]
            # 関数名が分からない場合は、Terraformの問題として既に記録済みなので読みに行かない。
            if not function_name:
                continue
            try:
                conditions[item.key] = self.read_conditions(function_name, region)
            # 認証切れなどは全関数に共通するため、同じ説明を重ねて表示しないよう一度で止める。
            except SettingsUnavailable as exc:
                problems.append(str(exc))
                break

        architecture, memory_mb = self._shared_conditions(implementations, conditions, problems)
        deployments: dict[str, dict[str, Any]] = {
            key: {
                "function_name": function_names[key],
                "architecture": condition.architecture,
                "memory_mb": condition.memory_mb,
            }
            for key, condition in conditions.items()
        }
        # 設定に問題がない場合だけ、デプロイ済みの版が手元のアプリと一致するかを確かめる。
        if not problems and self.check_revision is not None:
            for item in implementations:
                problem = self._check_image(item, function_names[item.key], region, deployments)
                if problem:
                    problems.append(problem)

        return LaunchSettings(
            implementations=implementations,
            endpoints=endpoints,
            log_groups={key: _log_group(name) for key, name in function_names.items()},
            architecture=architecture,
            memory_mb=memory_mb,
            region=region,
            problems=tuple(problems),
            deployments=deployments,
        )

    def _endpoint_override(self, item: Implementation) -> tuple[str, str | None]:
        """環境変数による接続先の上書きを、CLIと同じ優先順（API_ENDPOINT、API_URL）で返す。"""
        for suffix in ("API_ENDPOINT", "API_URL"):
            name = f"{item.env_prefix}_{suffix}"
            # 先に見つかった空でない値を上書きとして使い、どの変数かも案内に使う。
            if self.environ.get(name):
                return name, self.environ[name]
        return f"{item.env_prefix}_API_URL", None

    def _check_image(
        self,
        item: Implementation,
        function_name: str,
        region: str,
        deployments: dict[str, dict[str, Any]],
    ) -> str | None:
        """実イメージを一度だけ読み、版を照合したうえでdigestを証跡へ加える。"""
        try:
            image = self.read_image(function_name, region)
        # 権限不足や関数の未作成などで読めなければ、版を確認できないまま計測させない。
        except (ClientError, BotoCoreError) as exc:
            return f"{item.label} のデプロイ済みイメージを確認できませんでした（{exc}）。"
        deployments.setdefault(item.key, {}).update(
            {
                "image_repository": image.repository,
                "image_digest": image.digest or None,
                "image_tags": list(image.tags),
            }
        )
        assert self.check_revision is not None
        return self.check_revision(
            label=item.label,
            function_name=function_name,
            source_pathspecs=item.image_source_pathspecs,
            region=region,
            read_image=lambda _function_name, _region: image,
        )

    def _shared_conditions(
        self,
        implementations: tuple[Implementation, ...],
        conditions: dict[str, FunctionConditions],
        problems: list[str],
    ) -> tuple[str | None, int | None]:
        """全関数の実設定が一致する場合だけ、比較条件として採用する。"""
        # いずれかの実設定を読めていなければ、条件を確定できない。
        if set(conditions) != {item.key for item in implementations}:
            return None, None
        distinct = set(conditions.values())
        # 条件が異なる関数同士の計測は比較に使えないため、計測を開始させない。
        if len(distinct) > 1:
            detail = "、".join(
                f"{item.label}: {condition.architecture}・{condition.memory_mb} MB"
                for item in implementations
                if (condition := conditions[item.key])
            )
            problems.append(
                f"言語ごとの Lambda で設定が異なります（{detail}）。"
                "同じ設定にそろえてから計測してください。"
            )
            return None, None
        shared = distinct.pop()

        # 環境変数で条件が指定されていて実設定と食い違う場合は、記録が誤るため利用者に確認させる。
        expected_architecture = self.environ.get("ARCHITECTURE")
        expected_memory = self.environ.get("LAMBDA_MEMORY_MB")
        if (expected_architecture and expected_architecture != shared.architecture) or (
            expected_memory and expected_memory != str(shared.memory_mb)
        ):
            problems.append(
                f"環境変数の条件（ARCHITECTURE={expected_architecture or '未設定'}、"
                f"LAMBDA_MEMORY_MB={expected_memory or '未設定'}）が、"
                f"Lambda の実設定（{shared.architecture}・{shared.memory_mb} MB）と異なります。"
                ".env を実設定に合わせてください。"
            )
            return None, None
        return shared.architecture, shared.memory_mb


def _log_group(function_name: str | None) -> str | None:
    return f"/aws/lambda/{function_name}" if function_name else None
