"""DynamoDB属性変換、未登録、SDK障害、保存形式不正の扱いを検証する。"""

import asyncio
from decimal import Decimal
from threading import get_ident

import pytest
from botocore.exceptions import ClientError

from app.models import Item
from app.repositories import (
    DynamoDBItemRepository,
    InMemoryItemRepository,
    RepositoryError,
)
from app.services import ItemService


class FakeDynamoDBClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.thread_id: int | None = None

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.thread_id = get_ident()
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_dynamodb_repository_deserializes_item_in_worker_thread() -> None:
    client = FakeDynamoDBClient(
        {
            "Item": {
                "id": {"S": "item-001"},
                "value": {"N": "123"},
                "payload": {"S": "payload"},
            }
        }
    )
    repository = DynamoDBItemRepository(client, "items")
    event_loop_thread = get_ident()

    result = await repository.get_item("item-001")

    assert result == Item(id="item-001", value=123, payload="payload")
    assert client.thread_id != event_loop_thread
    assert client.calls == [
        {
            "TableName": "items",
            "Key": {"id": {"S": "item-001"}},
            "ConsistentRead": True,
        }
    ]


@pytest.mark.asyncio
async def test_dynamodb_repository_returns_none_for_missing_item() -> None:
    repository = DynamoDBItemRepository(FakeDynamoDBClient({}), "items")

    assert await repository.get_item("missing") is None


@pytest.mark.asyncio
async def test_dynamodb_repository_rejects_non_integer_value() -> None:
    client = FakeDynamoDBClient(
        {
            "Item": {
                "id": {"S": "item-001"},
                "value": {"N": "1.5"},
                "payload": {"S": "payload"},
            }
        }
    )

    with pytest.raises(RepositoryError, match="invalid shape"):
        await DynamoDBItemRepository(client, "items").get_item("item-001")


class ErrorDynamoDBClient:
    def get_item(self, **_kwargs: object) -> None:
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
            "GetItem",
        )


@pytest.mark.asyncio
async def test_dynamodb_repository_wraps_sdk_errors() -> None:
    with pytest.raises(RepositoryError, match="get_item failed"):
        await DynamoDBItemRepository(ErrorDynamoDBClient(), "items").get_item("item-001")


@pytest.mark.asyncio
async def test_in_memory_repository_is_a_test_boundary() -> None:
    expected = Item(id="item-001", value=1, payload="payload")
    repository = InMemoryItemRepository({"item-001": expected})

    assert await repository.get_item("item-001") == expected
    assert await repository.get_item("missing") is None


class ObservedRepository:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def get_item(self, item_id: str) -> Item:
        self.calls.append(item_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        return Item(id=item_id, value=1, payload=item_id)


@pytest.mark.asyncio
async def test_aggregate_concurrent_overlaps_all_repository_calls() -> None:
    repository = ObservedRepository()
    service = ItemService(repository)

    result = await service.aggregate(["c", "a", "b"], "concurrent")

    assert result.count == 3
    assert repository.calls == ["c", "a", "b"]
    assert repository.max_active == 3


@pytest.mark.asyncio
async def test_aggregate_sequential_does_not_overlap_repository_calls() -> None:
    repository = ObservedRepository()
    service = ItemService(repository)

    result = await service.aggregate(["c", "a", "b"], "sequential")

    assert result.count == 3
    assert repository.calls == ["c", "a", "b"]
    assert repository.max_active == 1


def test_decimal_fixture_documents_dynamodb_number_semantics() -> None:
    assert Decimal("123") == 123
