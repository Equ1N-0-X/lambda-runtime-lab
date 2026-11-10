"""Lambdaの設定を更新して、次の呼び出しを新しい実行環境（コールドスタート）で起こす。

Lambdaは関数の設定が変わると、既存の実行環境を使わず新しい実行環境で次の呼び出しを処理する。
計測専用の環境変数の値を毎回変えて、待たずに意図したコールドスタートを起こす。
計測の後は環境変数を取り除き、Terraformで管理している設定へ戻す。
"""

from __future__ import annotations

import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# 計測のためだけに書き換える環境変数。アプリはこの値を読まない。
NONCE_VARIABLE = "BENCHMARK_COLD_START_NONCE"


class ColdStartError(RuntimeError):
    """関数の設定を更新できず、コールドスタートを起こせなかったことを表す。"""


class LambdaColdStarter:
    """関数の環境変数を書き換えて新しい実行環境を起こし、計測後に元へ戻す。"""

    def __init__(self, region: str, client: Any | None = None) -> None:
        self._client = client or boto3.client("lambda", region_name=region)

    def force(self, function_name: str) -> None:
        """計測用の環境変数に新しい値を入れ、更新が反映されるまで待つ。"""
        variables = self._variables(function_name)
        variables[NONCE_VARIABLE] = uuid.uuid4().hex
        self._update(function_name, variables)

    def restore(self, function_name: str) -> None:
        """計測用の環境変数を取り除き、Terraformで管理している設定へ戻す。"""
        # 中止された場合は更新の途中のことがあるため、終わるのを待ってから読み直す。
        try:
            self._client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        except (BotoCoreError, ClientError) as exc:
            raise ColdStartError(
                f"Lambda 関数 {function_name} の更新が終わりません: {exc}"
            ) from exc
        variables = self._variables(function_name)
        # 計測用の値が残っていなければ、設定を変えずに終える。
        if NONCE_VARIABLE not in variables:
            return
        del variables[NONCE_VARIABLE]
        self._update(function_name, variables)

    def _variables(self, function_name: str) -> dict[str, str]:
        try:
            configuration = self._client.get_function_configuration(FunctionName=function_name)
        except (BotoCoreError, ClientError) as exc:
            raise ColdStartError(f"Lambda 関数 {function_name} の設定を読めません: {exc}") from exc
        return dict((configuration.get("Environment") or {}).get("Variables") or {})

    def _update(self, function_name: str, variables: dict[str, str]) -> None:
        # 環境変数は全体を置き換える仕様のため、既存の値を含めて渡す。
        # 更新の完了を待ってから呼び出さないと、古い実行環境で処理されることがある。
        try:
            self._client.update_function_configuration(
                FunctionName=function_name, Environment={"Variables": variables}
            )
            self._client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        except (BotoCoreError, ClientError) as exc:
            raise ColdStartError(
                f"Lambda 関数 {function_name} の設定を更新できません"
                f"（lambda:UpdateFunctionConfiguration の権限を確認してください）: {exc}"
            ) from exc
