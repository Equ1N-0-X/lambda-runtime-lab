"""DynamoDBとインメモリ保存先を、共通の項目取得インターフェースとして提供する。"""

import asyncio
import re
from collections.abc import Mapping
from typing import Any, Protocol

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import BotoCoreError, ClientError

from app.config import SETTINGS
from app.models import Item

# 保存先の障害を、調査の手掛かりとして内部ログに残す原因の分類。公開する応答には含めない。
PERMISSION_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "ExpiredTokenException",
        "InvalidSignatureException",
    }
)
THROTTLING_CODES = frozenset(
    {"ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded"}
)
# 保存値の数値は、DynamoDBが正規化して返す10進整数の表記だけを受け付ける。全言語で共通の規則。
INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")


class RepositoryError(Exception):
    """外部項目ストアの通信失敗または保存データ不正を、原因の分類とともにAPI層へ伝える。"""

    def __init__(self, category: str, error_code: str | None = None) -> None:
        super().__init__(category)
        # permission・table・throttling・network・invalid_item・unknown のいずれか。
        self.category = category
        # AWSが返したエラーコード。通信の失敗や保存値の不正ではNone。
        self.error_code = error_code


def classify_client_error(error: ClientError) -> RepositoryError:
    """AWSのエラーコードから、権限・テーブル・流量制限・その他の原因に分類する。"""
    code = error.response.get("Error", {}).get("Code")
    # 認証・権限の問題、テーブルの不在、流量制限は、それぞれ対処が異なるため分けて残す。
    if code in PERMISSION_CODES:
        return RepositoryError("permission", code)
    if code == "ResourceNotFoundException":
        return RepositoryError("table", code)
    if code in THROTTLING_CODES:
        return RepositoryError("throttling", code)
    return RepositoryError("unknown", code)


class ItemRepository(Protocol):
    """保存先を問わず、IDで項目を一件取得するための境界。"""

    async def get_item(self, item_id: str) -> Item | None: ...


class DynamoDBItemRepository:
    """DynamoDBの属性表現をドメインのItemへ変換して取得する。"""

    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table_name = table_name
        self._deserializer = TypeDeserializer()

    async def get_item(self, item_id: str) -> Item | None:
        """強整合読み取りを一回実行し、未登録ならNone、障害ならRepositoryErrorを返す。"""
        try:
            response = await asyncio.to_thread(
                self._client.get_item,
                TableName=self._table_name,
                Key={"id": {"S": item_id}},
                ConsistentRead=True,
            )
        # AWSが応答したエラーは、エラーコードから原因を分類して依存先障害へ変換する。
        except ClientError as error:
            raise classify_client_error(error) from error
        # 応答を得られない通信・タイムアウト等は、通信の問題として依存先障害へ変換する。
        except BotoCoreError as error:
            raise RepositoryError("network") from error

        raw_item = response.get("Item")

        # DynamoDBがItem属性を返さなかった場合は、指定IDが未登録として扱う。
        if raw_item is None:
            return None

        try:
            number = raw_item["value"]["N"]
            # value が10進整数の表記でない場合は、保存データが契約を満たさないため依存先障害にする。
            if not isinstance(number, str) or not INTEGER_PATTERN.fullmatch(number):
                raise ValueError("valueは10進整数である必要があります")
            item = {key: self._deserializer.deserialize(value) for key, value in raw_item.items()}
            # 公開するItemの境界であるIDの形式・値の範囲・payloadの長さは、モデルの検証で確かめる。
            return Item(id=item["id"], value=int(number), payload=item["payload"])
        # 必須属性の欠落、属性型の不一致、数値形式・範囲の不正は、利用者入力ではなく
        # 保存データ不正として扱う。
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError("invalid_item") from error


class InMemoryItemRepository:
    """テストで外部I/Oなしに同じ取得契約を再現する保存先。"""

    def __init__(self, items: Mapping[str, Item] | None = None) -> None:
        self._items = dict(items or {})

    async def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)


DYNAMODB_CLIENT = boto3.client("dynamodb", region_name=SETTINGS.aws_region)
DYNAMODB_REPOSITORY = DynamoDBItemRepository(DYNAMODB_CLIENT, SETTINGS.table_name)
