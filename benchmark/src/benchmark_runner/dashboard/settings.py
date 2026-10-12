"""画面から計測するための接続先と比較条件を、TerraformとAWS上のLambdaの実設定から求める。

利用者が環境変数を手で用意しなくても、デプロイ済みの環境をそのまま計測できるようにする。
環境変数で接続先が指定されていれば、make benchmark と同じくそちらを優先する。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

DEFAULT_REGION = "ap-northeast-1"
IMPLEMENTATIONS = ("python", "rust")
LABELS = {"python": "Python", "rust": "Rust"}
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
    """計測の接続先と、比較条件として記録するLambdaの実設定。"""

    python_endpoint: str | None
    rust_endpoint: str | None
    architecture: str | None
    memory_mb: int | None
    region: str
    python_log_group: str | None
    rust_log_group: str | None
    # 取得元を読めなかった理由や条件の不一致など、値の欠落だけでは表せない問題。
    problems: tuple[str, ...] = ()

    def missing(self) -> list[str]:
        """計測を開始できない理由を列挙する。問題の説明を先に、欠けている値を後に並べる。"""
        checks = (
            (self.python_endpoint, "Python API の URL"),
            (self.rust_endpoint, "Rust API の URL"),
            (self.architecture, "Lambda のアーキテクチャ"),
            (self.memory_mb, "Lambda のメモリ"),
            (self.python_log_group, "Python のロググループ"),
            (self.rust_log_group, "Rust のロググループ"),
        )
        absent = [label for value, label in checks if not value]
        # 取得元の問題で値が欠けている場合、欠落項目の羅列より問題の説明の方が対処に役立つ。
        if self.problems:
            return list(self.problems)
        return [f"{label}を取得できませんでした。" for label in absent]

    def public(self) -> dict[str, Any]:
        """画面に返してよい項目だけを返す。URLやロググループ名は含めない。"""
        missing = self.missing()
        return {
            "ready": not missing,
            "missing": missing,
            "architecture": self.architecture,
            "memory_mb": self.memory_mb,
            "region": self.region,
        }

    def secrets(self) -> list[str]:
        """実行出力から伏せる接続先の文字列。"""
        return [value for value in (self.python_endpoint, self.rust_endpoint) if value]


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
        environ: Mapping[str, str] | None = None,
        read_outputs: Callable[[Path], dict[str, Any]] = read_terraform_outputs,
        read_conditions: Callable[[str, str], FunctionConditions] = read_lambda_conditions,
    ) -> None:
        self.terraform_dir = terraform_dir
        self.environ = os.environ if environ is None else environ
        self.read_outputs = read_outputs
        self.read_conditions = read_conditions

    def resolve(self) -> LaunchSettings:
        region = self.environ.get("AWS_REGION") or DEFAULT_REGION
        problems: list[str] = []

        outputs: dict[str, Any] = {}
        try:
            outputs = self.read_outputs(self.terraform_dir)
        except SettingsUnavailable as exc:
            problems.append(str(exc))

        # 接続先は make benchmark と同じ環境変数を優先し、なければTerraformの出力を使う。
        endpoints = {
            name: self.environ.get(f"{name.upper()}_API_URL") or outputs.get(f"{name}_api_endpoint")
            for name in IMPLEMENTATIONS
        }
        function_names = {name: outputs.get(f"{name}_lambda_name") for name in IMPLEMENTATIONS}

        conditions: dict[str, FunctionConditions] = {}
        for name, function_name in function_names.items():
            # 関数名が分からない場合は、Terraformの問題として既に記録済みなので読みに行かない。
            if not function_name:
                continue
            try:
                conditions[name] = self.read_conditions(function_name, region)
            # 認証切れなどは両関数に共通するため、同じ説明を重ねて表示しないよう一度で止める。
            except SettingsUnavailable as exc:
                problems.append(str(exc))
                break

        architecture, memory_mb = self._shared_conditions(conditions, problems)
        return LaunchSettings(
            python_endpoint=endpoints["python"],
            rust_endpoint=endpoints["rust"],
            architecture=architecture,
            memory_mb=memory_mb,
            region=region,
            python_log_group=_log_group(function_names["python"]),
            rust_log_group=_log_group(function_names["rust"]),
            problems=tuple(problems),
        )

    def _shared_conditions(
        self, conditions: dict[str, FunctionConditions], problems: list[str]
    ) -> tuple[str | None, int | None]:
        """両関数の実設定が一致する場合だけ、比較条件として採用する。"""
        # どちらかの実設定を読めていなければ、条件を確定できない。
        if set(conditions) != set(IMPLEMENTATIONS):
            return None, None
        python, rust = conditions["python"], conditions["rust"]
        # 条件が異なる関数同士の計測は比較に使えないため、計測を開始させない。
        if python != rust:
            problems.append(
                "Python と Rust の Lambda で設定が異なります"
                f"（Python: {python.architecture}・{python.memory_mb} MB、"
                f"Rust: {rust.architecture}・{rust.memory_mb} MB）。"
                "同じ設定にそろえてから計測してください。"
            )
            return None, None

        # 環境変数で条件が指定されていて実設定と食い違う場合は、記録が誤るため利用者に確認させる。
        expected_architecture = self.environ.get("ARCHITECTURE")
        expected_memory = self.environ.get("LAMBDA_MEMORY_MB")
        if (expected_architecture and expected_architecture != python.architecture) or (
            expected_memory and expected_memory != str(python.memory_mb)
        ):
            problems.append(
                f"環境変数の条件（ARCHITECTURE={expected_architecture or '未設定'}、"
                f"LAMBDA_MEMORY_MB={expected_memory or '未設定'}）が、"
                f"Lambda の実設定（{python.architecture}・{python.memory_mb} MB）と異なります。"
                ".env を実設定に合わせてください。"
            )
            return None, None
        return python.architecture, python.memory_mb


def _log_group(function_name: str | None) -> str | None:
    return f"/aws/lambda/{function_name}" if function_name else None
