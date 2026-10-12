"""実行環境変数を、アプリケーションが利用する不変設定へ変換する。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """DynamoDBの接続先を決める、起動後に変更しない設定値。"""

    table_name: str
    aws_region: str


def load_settings() -> Settings:
    """環境変数が未指定の場合は、ローカル比較で使う既定のテーブル名とリージョンを採用する。"""
    return Settings(
        table_name=os.getenv("DYNAMODB_TABLE_NAME", "lambda-runtime-lab-items"),
        aws_region=os.getenv("AWS_REGION", "ap-northeast-1"),
    )


SETTINGS = load_settings()
