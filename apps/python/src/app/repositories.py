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
    """Raised when an external item store cannot serve a request."""


class ItemRepository(Protocol):
    async def get_item(self, item_id: str) -> Item | None: ...


class DynamoDBItemRepository:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table_name = table_name
        self._deserializer = TypeDeserializer()

    async def get_item(self, item_id: str) -> Item | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_item,
                TableName=self._table_name,
                Key={"id": {"S": item_id}},
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as error:
            raise RepositoryError("DynamoDB get_item failed") from error

        raw_item = response.get("Item")
        if raw_item is None:
            return None

        try:
            item = {key: self._deserializer.deserialize(value) for key, value in raw_item.items()}
            value = item["value"]
            if not isinstance(value, Decimal) or value != value.to_integral_value():
                raise ValueError("value must be an integer")
            return Item(id=item["id"], value=int(value), payload=item["payload"])
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError("DynamoDB item has an invalid shape") from error


class InMemoryItemRepository:
    def __init__(self, items: Mapping[str, Item] | None = None) -> None:
        self._items = dict(items or {})

    async def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)


DYNAMODB_CLIENT = boto3.client("dynamodb", region_name=SETTINGS.aws_region)
DYNAMODB_REPOSITORY = DynamoDBItemRepository(DYNAMODB_CLIENT, SETTINGS.table_name)
