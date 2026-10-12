"""ベンチマーク入力が正規API契約を満たすことを検証する。

契約違反のワークロード本文やseed項目は、デプロイ後の全要求を4xx/5xxにする。このテストは
上限値をOpenAPIから読み、契約を重複定義せずデプロイ前に不整合を検出する。
"""

import re
import runpy
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _document(relative_path: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return _document("spec/openapi.yaml")


@pytest.fixture(scope="module")
def workloads() -> dict[str, Any]:
    return _document("spec/workloads.yaml")["workloads"]


@pytest.fixture(scope="module")
def seed_items() -> list[dict[str, Any]]:
    generate_items = runpy.run_path(str(ROOT / "scripts/seed-data.py"))["generate_items"]
    return generate_items("benchmark-seed")


def _schema(contract: dict[str, Any], name: str) -> dict[str, Any]:
    return contract["components"]["schemas"][name]


def _assert_string(value: Any, schema: dict[str, Any], label: str) -> None:
    assert isinstance(value, str), f"{label} must be a string"
    if "minLength" in schema:
        assert len(value) >= schema["minLength"], f"{label} is too short"
    if "maxLength" in schema:
        assert len(value) <= schema["maxLength"], f"{label} is too long"
    if "pattern" in schema:
        assert re.fullmatch(schema["pattern"], value), f"{label} does not match the contract"


def _assert_integer(value: Any, schema: dict[str, Any], label: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), f"{label} must be an integer"
    assert value >= schema["minimum"], f"{label} is below the contract minimum"
    assert value <= schema["maximum"], f"{label} is above the contract maximum"


def _work_limit(contract: dict[str, Any]) -> int:
    reason = contract["x-canonical-contract"]["validation"]["reason-vocabulary"]["work-limit"]
    return int(reason.split(":", 1)[1])


def test_transform_workloads_satisfy_the_contract(contract, workloads) -> None:
    request_schema = _schema(contract, "TransformRequest")["properties"]["records"]
    record_schema = _schema(contract, "TransformRecordInput")["properties"]
    item_id = _schema(contract, "ItemId")
    item_value = _schema(contract, "ItemValue")

    for name, workload in workloads.items():
        if workload["path"] != "/transform":
            continue
        records = workload["body"]["records"]
        assert request_schema["minItems"] <= len(records) <= request_schema["maxItems"], name
        assert len({record["id"] for record in records}) == len(records), (
            f"{name}: duplicate record IDs are rejected by the contract"
        )
        for index, record in enumerate(records):
            label = f"{name}.records[{index}]"
            assert set(record) == set(record_schema), f"{label} must use exactly the contract keys"
            _assert_string(record["id"], item_id, f"{label}.id")
            _assert_string(record["name"], record_schema["name"], f"{label}.name")
            _assert_integer(record["value"], item_value, f"{label}.value")
            tags = record["tags"]
            assert len(tags) <= record_schema["tags"]["maxItems"], f"{label}.tags"
            for tag_index, tag in enumerate(tags):
                _assert_string(tag, record_schema["tags"]["items"], f"{label}.tags[{tag_index}]")


def test_compute_workloads_stay_within_the_work_limit(contract, workloads) -> None:
    properties = _schema(contract, "ComputeRequest")["properties"]
    supported = properties["profile"]["x-supported-values"]
    limit = _work_limit(contract)

    for name, workload in workloads.items():
        if workload["path"] != "/compute":
            continue
        body = workload["body"]
        assert set(body) == set(properties), f"{name} must use exactly the contract keys"
        _assert_integer(body["items"], properties["items"], f"{name}.items")
        _assert_integer(body["iterations"], properties["iterations"], f"{name}.iterations")
        _assert_string(body["seed"], properties["seed"], f"{name}.seed")
        assert body["profile"] in supported, f"{name}.profile is not an MVP profile"
        assert body["items"] * body["iterations"] <= limit, f"{name} exceeds the work limit"


def test_aggregate_workloads_reference_seeded_items(contract, workloads, seed_items) -> None:
    properties = _schema(contract, "AggregateRequest")["properties"]
    supported = properties["profile"]["x-supported-values"]
    seeded = {item["id"] for item in seed_items}

    for name, workload in workloads.items():
        if workload["path"] != "/aggregate":
            continue
        body = workload["body"]
        ids = body["ids"]
        assert properties["ids"]["minItems"] <= len(ids) <= properties["ids"]["maxItems"], name
        assert len(set(ids)) == len(ids), f"{name}: duplicate IDs are rejected by the contract"
        assert body["profile"] in supported, f"{name}.profile is not an MVP profile"
        for index, item_id in enumerate(ids):
            _assert_string(item_id, _schema(contract, "ItemId"), f"{name}.ids[{index}]")
            assert item_id in seeded, f"{name}.ids[{index}] is not present in the seed data"


def test_item_workloads_reference_seeded_items(contract, workloads, seed_items) -> None:
    seeded = {item["id"] for item in seed_items}
    paths = [
        workload["path"]
        for workload in workloads.values()
        if workload["path"].startswith("/items/")
    ]
    assert paths, "the benchmark matrix requires at least one single-item workload"

    for path in paths:
        item_id = path.removeprefix("/items/")
        _assert_string(item_id, _schema(contract, "ItemId"), path)
        assert item_id in seeded, f"{path} is not present in the seed data"


def test_seed_items_satisfy_the_item_schema(contract, seed_items) -> None:
    item_schema = _schema(contract, "Item")["properties"]

    assert seed_items, "seed data must not be empty"
    for item in seed_items:
        assert set(item) == set(item_schema), "seed items must use exactly the contract keys"
        _assert_string(item["id"], _schema(contract, "ItemId"), f"{item['id']}.id")
        _assert_integer(item["value"], _schema(contract, "ItemValue"), f"{item['id']}.value")
        _assert_string(item["payload"], _schema(contract, "Payload"), f"{item['id']}.payload")
