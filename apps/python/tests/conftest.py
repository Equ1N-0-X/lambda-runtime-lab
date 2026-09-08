import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

from app.main import create_app
from app.models import Item
from app.repositories import InMemoryItemRepository


@pytest.fixture
def items() -> dict[str, Item]:
    return {
        "item-001": Item(id="item-001", value=123, payload="payload-c"),
        "item-002": Item(id="item-002", value=100, payload="payload-a"),
        "item-003": Item(id="item-003", value=146, payload="payload-b"),
    }


@pytest.fixture
def client(items: dict[str, Item]) -> TestClient:
    return TestClient(create_app(InMemoryItemRepository(items)))
