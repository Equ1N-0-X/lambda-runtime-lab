"""DynamoDB属性変換、未登録、SDK障害、保存形式不正の扱いを検証する。"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from threading import get_ident
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

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

    with pytest.raises(RepositoryError, match="invalid_item"):
        await DynamoDBItemRepository(client, "items").get_item("item-001")


class ErrorDynamoDBClient:
    def get_item(self, **_kwargs: object) -> None:
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
            "GetItem",
        )


@pytest.mark.asyncio
async def test_dynamodb_repository_wraps_sdk_errors() -> None:
    with pytest.raises(RepositoryError) as error:
        await DynamoDBItemRepository(ErrorDynamoDBClient(), "items").get_item("item-001")

    assert error.value.category == "throttling"
    assert error.value.error_code == "ProvisionedThroughputExceededException"


class RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_item(self, **_kwargs: object) -> None:
        raise self.error


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "secret detail"}}, "GetItem")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "category", "code"),
    [
        (_client_error("AccessDeniedException"), "permission", "AccessDeniedException"),
        (_client_error("ResourceNotFoundException"), "table", "ResourceNotFoundException"),
        (_client_error("InternalServerError"), "unknown", "InternalServerError"),
        (EndpointConnectionError(endpoint_url="https://dynamodb.invalid"), "network", None),
    ],
)
async def test_sdk_errors_are_classified_by_cause(
    error: Exception, category: str, code: str | None
) -> None:
    with pytest.raises(RepositoryError) as raised:
        await DynamoDBItemRepository(RaisingClient(error), "items").get_item("item-001")

    assert (raised.value.category, raised.value.error_code) == (category, code)


STORED_ITEM_CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "spec/repository-cases/stored-items.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", STORED_ITEM_CASES, ids=[case["name"] for case in STORED_ITEM_CASES]
)
async def test_stored_item_conversion_follows_shared_cases(case: dict[str, Any]) -> None:
    repository = DynamoDBItemRepository(FakeDynamoDBClient({"Item": case["attributes"]}), "items")

    # 全言語で同じ保存値の境界を使い、契約外の保存値を成功の応答にしない。
    if "error" in case["expected"]:
        with pytest.raises(RepositoryError) as raised:
            await repository.get_item("item-001")
        assert raised.value.category == case["expected"]["error"]
    else:
        assert await repository.get_item("item-001") == Item(**case["expected"]["item"])


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
