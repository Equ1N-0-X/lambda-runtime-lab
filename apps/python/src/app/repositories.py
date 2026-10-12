"""DynamoDBとインメモリ保存先を、共通の項目取得インターフェースとして提供する。"""

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import BotoCoreError, ClientError

from app.config import SETTINGS
from app.models import Item


class RepositoryError(Exception):
    """外部項目ストアの通信失敗または保存データ不正をAPI層へ伝える。"""


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
        # AWS SDKの通信・認証・サービスエラーなら、詳細を外部へ出さず依存先障害として変換する。
        except (BotoCoreError, ClientError) as error:
            raise RepositoryError("DynamoDB get_item failed") from error

        raw_item = response.get("Item")

        # DynamoDBがItem属性を返さなかった場合は、指定IDが未登録として扱う。
        if raw_item is None:
            return None

        try:
            item = {key: self._deserializer.deserialize(value) for key, value in raw_item.items()}
            value = item["value"]
            # value が10進整数でない場合は、保存データがAPI契約を満たさないため依存先障害にする。
            if not isinstance(value, Decimal) or value != value.to_integral_value():
                raise ValueError("valueは整数である必要があります")
            return Item(id=item["id"], value=int(value), payload=item["payload"])
        # 必須属性の欠落、属性型の不一致、数値形式の不正は、利用者入力ではなく
        # 保存データ不正として扱う。
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError("DynamoDB item has an invalid shape") from error


class InMemoryItemRepository:
    """テストで外部I/Oなしに同じ取得契約を再現する保存先。"""

    def __init__(self, items: Mapping[str, Item] | None = None) -> None:
        self._items = dict(items or {})

    async def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)


DYNAMODB_CLIENT = boto3.client("dynamodb", region_name=SETTINGS.aws_region)
DYNAMODB_REPOSITORY = DynamoDBItemRepository(DYNAMODB_CLIENT, SETTINGS.table_name)
