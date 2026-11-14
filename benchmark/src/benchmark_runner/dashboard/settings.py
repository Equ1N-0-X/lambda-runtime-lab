"""画面から計測するための接続先と比較条件を、TerraformとAWS上のLambdaの実設定から求める。

利用者が環境変数を手で用意しなくても、デプロイ済みの環境をそのまま計測できるようにする。
環境変数で接続先が指定されていれば、make benchmark と同じくそちらを優先する。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class Problem:
    """計測を開始できない理由を、何が悪いか・対象・対処・対処の結果に分けて表す。

    画面が、原因を一目で読める見出しと、実行するコマンド、実行後にどうなるかを
    別々に示せるよう、一つの文章にまとめずに持つ。
    """

    # 何が悪いかを一行で表す見出し。
    summary: str
    # 問題の対象（環境変数名、言語名など）。見出しの下に箇条書きで示す。
    items: tuple[str, ...] = ()
    # 利用者が行う対処。
    action: str | None = None
    # 対処として実行するコマンド。そのまま貼り付けて実行できる形で持つ。
    command: str | None = None
    # 対処するとどうなるか。
    effect: str | None = None

    def text(self) -> str:
        """画面以外（CLIの出力や例外の文言）で使う、一続きの文章を返す。"""
        parts = [self.summary, "・".join(self.items), self.action, self.command, self.effect]
        return " ".join(part for part in parts if part)


class SettingsUnavailable(Exception):
    """設定の取得元を読めない理由を、利用者が対処できる形で表す。"""

    def __init__(self, problem: Problem | str) -> None:
        # 文言だけを渡された場合は、対処の案内を持たない理由として扱う。
        self.problem = problem if isinstance(problem, Problem) else Problem(summary=problem)
        super().__init__(self.problem.text())


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
    problems: tuple[Problem, ...] = ()
    # 実装ごとに、計測先として確かめたLambda関数・実設定・イメージ。runの証跡として保存する。
    deployments: dict[str, dict[str, Any]] = field(default_factory=dict)

    def missing(self) -> list[str]:
        """計測を開始できない理由を、一続きの文章として列挙する。"""
        return [problem.text() for problem in self.blockers()]

    def blockers(self) -> list[Problem]:
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
        # 欠けた項目がなければ、計測を妨げる理由はない。
        if not absent:
            return []
        return [
            Problem(
                summary="計測に必要な設定を取得できませんでした",
                items=tuple(absent),
                action="AWS へのデプロイ状況を確認してから「設定を再確認」を押してください。",
            )
        ]

    def public(self) -> dict[str, Any]:
        """画面に返してよい項目だけを返す。URLやロググループ名は含めない。"""
        blockers = self.blockers()
        return {
            "ready": not blockers,
            "problems": [asdict(problem) for problem in blockers],
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
            Problem(
                summary="terraform コマンドが見つかりません",
                action="Terraform をインストールしてから「設定を再確認」を押してください。",
                effect="デプロイ済みの環境から、計測の接続先を読み取れるようになります。",
            )
        ) from exc
    # 出力がない、stateがないなどでTerraformが失敗した場合は、デプロイ前の可能性が高い。
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SettingsUnavailable(
            Problem(
                summary="Terraform の出力を読めません",
                action="AWS へのデプロイ（make deploy）が済んでいるか確認してください。",
                effect="デプロイが済んでいれば、計測の接続先を読み取れるようになります。",
            )
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
            Problem(
                summary="AWS の認証情報が見つからないか、期限が切れています",
                action="ターミナルで AWS にログインしてから「設定を再確認」を押してください。",
                effect="Lambda の設定を読み取れるようになり、計測を開始できます。",
            )
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        # 関数が存在しなければ、未デプロイか別のリージョン・環境名を参照している。
        if code == "ResourceNotFoundException":
            raise SettingsUnavailable(
                Problem(
                    summary=f"Lambda 関数 {function_name} が {region} に見つかりません",
                    action="デプロイ済みか、リージョン（AWS_REGION）が正しいか確認してください。",
                )
            ) from exc
        # 読み取り権限がなければ、利用中のAWSプロファイルの権限を見直す必要がある。
        if code in ("AccessDeniedException", "UnrecognizedClientException"):
            raise SettingsUnavailable(
                Problem(
                    summary="利用中の AWS 認証情報では Lambda の設定を読めません",
                    action="lambda:GetFunctionConfiguration の権限がある認証情報を使ってください。",
                )
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
        problems: list[Problem] = []

        outputs: dict[str, Any] = {}
        try:
            outputs = self.read_outputs(self.terraform_dir)
        except SettingsUnavailable as exc:
            problems.append(exc.problem)

        api_endpoints = outputs.get("api_endpoints") or {}
        lambda_names = outputs.get("lambda_names") or {}
        endpoints = {}
        mismatched_overrides: list[str] = []
        for item in implementations:
            name, override = self._endpoint_override(item)
            deployed = api_endpoints.get(item.key)
            endpoints[item.key] = override or deployed
            # 上書きした接続先がTerraformで管理するAPIと違えば、呼び出す先と、設定・ログを読む
            # Lambdaが別の環境になり、記録が実体と食い違うため計測させない。
            if override and deployed and override != deployed:
                mismatched_overrides.append(name)
        # 原因と対処が同じ問題を言語の数だけ並べないよう、該当する環境変数を一件にまとめて示す。
        if mismatched_overrides:
            problems.append(
                Problem(
                    summary="次の環境変数が、デプロイ済みの API とは別の接続先を指しています",
                    items=tuple(mismatched_overrides),
                    # 環境変数は画面の起動時に読むため、再確認だけでは反映されず起動し直しが要る。
                    action=(
                        "make ui を止め（Ctrl+C）、同じターミナルで次のコマンドを実行してから、"
                        "make ui を起動し直してください。"
                    ),
                    command=f"unset {' '.join(mismatched_overrides)}",
                    effect=(
                        "この環境変数による接続先の上書きがなくなり、"
                        "デプロイ済みの API を計測できるようになります。"
                    ),
                )
            )
        function_names = {item.key: lambda_names.get(item.key) for item in implementations}
        not_deployed = [item.label for item in implementations if not function_names[item.key]]
        # Terraformは読めたが一覧の実装がデプロイされていなければ、追加後のデプロイ漏れである。
        if outputs and not_deployed:
            problems.append(
                Problem(
                    summary=f"{'・'.join(not_deployed)} の Lambda がデプロイされていません",
                    action="次のコマンドを実行してから「設定を再確認」を押してください。",
                    command="make bootstrap-ecr && make publish-images && make deploy",
                    effect="この言語の Lambda が AWS 上に作られ、計測できるようになります。",
                )
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
                problems.append(exc.problem)
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
                # 版の確認は対処を含む文章で理由を返すため、そのまま見出しとして示す。
                if problem:
                    problems.append(Problem(summary=problem))

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
        problems: list[Problem],
    ) -> tuple[str | None, int | None]:
        """全関数の実設定が一致する場合だけ、比較条件として採用する。"""
        # いずれかの実設定を読めていなければ、条件を確定できない。
        if set(conditions) != {item.key for item in implementations}:
            return None, None
        distinct = set(conditions.values())
        # 条件が異なる関数同士の計測は比較に使えないため、計測を開始させない。
        if len(distinct) > 1:
            problems.append(
                Problem(
                    summary="言語ごとの Lambda で設定が異なります",
                    items=tuple(
                        f"{item.label}: {condition.architecture}・{condition.memory_mb} MB"
                        for item in implementations
                        if (condition := conditions[item.key])
                    ),
                    action=(
                        "Terraform で全言語を同じ設定にそろえてデプロイし、"
                        "「設定を再確認」を押してください。"
                    ),
                    effect="全言語を同じ条件で比べられるようになり、計測を開始できます。",
                )
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
                Problem(
                    summary=(
                        "次の環境変数の条件が、"
                        f"Lambda の実設定（{shared.architecture}・{shared.memory_mb} MB）"
                        "と異なります"
                    ),
                    items=(
                        f"ARCHITECTURE={expected_architecture or '未設定'}",
                        f"LAMBDA_MEMORY_MB={expected_memory or '未設定'}",
                    ),
                    # 環境変数は画面の起動時に読むため、直した後に起動し直しが要る。
                    action=(
                        ".env の値を実設定に合わせるか、これらの環境変数を外してから、"
                        "make ui を起動し直してください。"
                    ),
                    effect="記録する条件が実際の Lambda と一致し、計測を開始できます。",
                )
            )
            return None, None
        return shared.architecture, shared.memory_mb


def _log_group(function_name: str | None) -> str | None:
    return f"/aws/lambda/{function_name}" if function_name else None
