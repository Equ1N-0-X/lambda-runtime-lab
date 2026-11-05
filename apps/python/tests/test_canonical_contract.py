"""共有する正規テストベクトルをPython実装へ適用する。

`spec/testcases`を契約の正本とし、Rust側も同じファイルを使うことで、デプロイ環境なしに
両実装の応答を比較する。
"""

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Item
from app.repositories import InMemoryItemRepository

TESTCASE_DIRECTORY = Path(__file__).resolve().parents[3] / "spec" / "testcases"


def _testcase_files() -> list[Path]:
    files = sorted(TESTCASE_DIRECTORY.glob("*.json"))
    assert files, f"no canonical test cases found in {TESTCASE_DIRECTORY}"
    return files


def _documents() -> list[dict[str, Any]]:
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in _testcase_files()]
    # 一部の言語だけで実装済みのケースファイルは、Pythonが対象に含まれる場合だけ使う。
    return [
        document
        for document in documents
        if "python" in document.get("implementations", ["python"])
    ]


def _fixture_items() -> dict[str, Item]:
    items: dict[str, Item] = {}
    for document in _documents():
        for raw in document.get("fixtures", {}).get("items", []):
            items[raw["id"]] = Item(**raw)
    return items


def _cases() -> list[tuple[str, dict[str, Any]]]:
    return [(case["name"], case) for document in _documents() for case in document.get("cases", [])]


def _request_content(request: dict[str, Any]) -> bytes | None:
    """ケースの本文を送信するバイト列へ変換する。生の本文は組み立て直さずにそのまま送る。"""
    # raw_body・raw_body_base64は、JSONとして不正な本文や数値の表記をそのまま送るために使う。
    if "raw_body_base64" in request:
        return base64.b64decode(request["raw_body_base64"])
    if "raw_body" in request:
        return request["raw_body"].encode("utf-8")
    if "body" in request:
        return json.dumps(request["body"], ensure_ascii=False).encode("utf-8")
    return None


@pytest.fixture(scope="module")
def canonical_client() -> TestClient:
    return TestClient(create_app(InMemoryItemRepository(_fixture_items())))


@pytest.mark.parametrize(("name", "case"), _cases(), ids=[name for name, _ in _cases()])
def test_canonical_case(name: str, case: dict[str, Any], canonical_client: TestClient) -> None:
    request = case["request"]
    expected = case["expected"]

    content = _request_content(request)
    response = canonical_client.request(
        request["method"],
        request["path"],
        content=content,
        headers={"content-type": "application/json"} if content is not None else None,
    )

    assert response.status_code == expected["status"], name
    assert response.json() == expected["body"], name
