from app.domain import aggregate_items, compute_checksum, transform_records
from app.models import Item, TransformRecord


def test_transform_normalizes_and_orders_records() -> None:
    result = transform_records(
        [
            TransformRecord(id="b", name="  ZED  ", tags=["z", "a", "z"], value=2),
            TransformRecord(id="a", name=" Mixed Case ", tags=[], value=1),
        ]
    )

    assert result.model_dump() == {
        "records": [
            {"id": "a", "name": "mixed case", "tags": [], "value": 1},
            {"id": "b", "name": "zed", "tags": ["a", "z"], "value": 2},
        ],
        "count": 2,
    }


def test_compute_uses_raw_digest_as_next_iteration_input() -> None:
    result = compute_checksum(items=3, iterations=2, seed="benchmark-seed")

    assert result.model_dump() == {
        "processed": 3,
        "iterations": 2,
        "checksum": "4239a9c91e5b6190e724e15b64ba68ebc5d8afa877c2cfb37bee0bb539be4b47",
    }


def test_aggregate_sorts_by_id_before_hashing_payloads() -> None:
    result = aggregate_items(
        [
            Item(id="item-003", value=146, payload="payload-b"),
            Item(id="item-001", value=123, payload="payload-c"),
            Item(id="item-002", value=100, payload="payload-a"),
        ]
    )

    assert result.model_dump() == {
        "count": 3,
        "total_value": 369,
        "checksum": "9f4d3bc1b43ebd433793269c191f4afd4c81f1b7d881c7652b6af68190b2cad0",
    }
