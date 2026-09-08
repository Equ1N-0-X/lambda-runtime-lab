from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryError


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


def test_transform_normalizes_and_orders_records(client: TestClient) -> None:
    response = client.post(
        "/transform",
        json={
            "records": [
                {"id": "item-002", "name": "SECOND", "tags": ["z", "a"], "value": -7},
                {
                    "id": "item-001",
                    "name": "  Example Name  ",
                    "tags": ["b", "a", "b"],
                    "value": 123,
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "records": [
            {"id": "item-001", "name": "example name", "tags": ["a", "b"], "value": 123},
            {"id": "item-002", "name": "second", "tags": ["a", "z"], "value": -7},
        ],
        "count": 2,
    }


def test_validation_details_use_the_canonical_vocabulary(client: TestClient) -> None:
    response = client.post(
        "/transform",
        json={"records": [{"name": "Example", "tags": [], "value": "123", "unexpected": True}]},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "request validation failed",
            "details": [
                {"field": "records[0].id", "reason": "required"},
                {"field": "records[0].unexpected", "reason": "additional_property_not_allowed"},
                {"field": "records[0].value", "reason": "type:integer"},
            ],
        }
    }


def test_transform_rejects_empty_records(client: TestClient) -> None:
    response = client.post("/transform", json={"records": []})

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "records", "reason": "min_items:1"}]


def test_transform_rejects_duplicate_record_ids(client: TestClient) -> None:
    response = client.post(
        "/transform",
        json={
            "records": [
                {"id": "item-001", "name": "First", "tags": [], "value": 1},
                {"id": "item-001", "name": "Second", "tags": [], "value": 2},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "records[1].id", "reason": "duplicate"}
    ]


def test_malformed_json_reports_only_the_root_field(client: TestClient) -> None:
    response = client.post(
        "/compute",
        content=b'{"items":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "request validation failed",
            "details": [{"field": "$", "reason": "invalid_json"}],
        }
    }


def test_duplicate_object_members_are_invalid_json(client: TestClient) -> None:
    response = client.post(
        "/compute",
        content=(
            b'{"items": 1, "items": 2, "iterations": 1, "seed": "s", "profile": "sequential"}'
        ),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "$", "reason": "invalid_json"}]


def test_compute_returns_the_canonical_digest(client: TestClient) -> None:
    response = client.post(
        "/compute",
        json={"items": 3, "iterations": 2, "seed": "benchmark-seed", "profile": "sequential"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "processed": 3,
        "iterations": 2,
        "checksum": "4239a9c91e5b6190e724e15b64ba68ebc5d8afa877c2cfb37bee0bb539be4b47",
    }


def test_compute_does_not_normalize_utf8_seeds(client: TestClient) -> None:
    response = client.post(
        "/compute",
        json={"items": 2, "iterations": 3, "seed": "e\u0301", "profile": "sequential"},
    )

    assert response.status_code == 200
    assert (
        response.json()["checksum"]
        == "a275dc01e31c0b02098fbf15ffb92b7272fa88e9c3ca6efc97c178c7102b34eb"
    )


def test_compute_rejects_zero_items(client: TestClient) -> None:
    response = client.post(
        "/compute",
        json={"items": 0, "iterations": 2, "seed": "benchmark-seed", "profile": "sequential"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "items", "reason": "minimum:1"}]


def test_compute_rejects_work_over_the_limit(client: TestClient) -> None:
    response = client.post(
        "/compute",
        json={
            "items": 1000,
            "iterations": 1001,
            "seed": "benchmark-seed",
            "profile": "sequential",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "$", "reason": "maximum_work:1000000"}]


def test_compute_rejects_unsupported_profile(client: TestClient) -> None:
    response = client.post(
        "/compute",
        json={"items": 1, "iterations": 1, "seed": "seed", "profile": "parallel"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "UNSUPPORTED_PROFILE",
        "message": "profile is not supported",
        "details": [{"field": "profile", "reason": "supported_values:sequential"}],
    }


def test_get_item_returns_the_stored_item(client: TestClient) -> None:
    response = client.get("/items/item-001")

    assert response.status_code == 200
    assert response.json() == {"item": {"id": "item-001", "value": 123, "payload": "payload-c"}}


def test_get_item_returns_the_canonical_not_found_error(client: TestClient) -> None:
    response = client.get("/items/item-999")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "item not found",
            "details": [{"field": "id", "reason": "not_found:item-999"}],
        }
    }


def test_get_item_rejects_ids_outside_the_contract_pattern(client: TestClient) -> None:
    response = client.get("/items/-invalid")

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "id", "reason": "pattern"}]


def test_aggregate_profiles_produce_identical_results(client: TestClient) -> None:
    expected = {
        "count": 3,
        "total_value": 369,
        "checksum": "9f4d3bc1b43ebd433793269c191f4afd4c81f1b7d881c7652b6af68190b2cad0",
    }

    for profile in ("sequential", "concurrent"):
        response = client.post(
            "/aggregate",
            json={"ids": ["item-003", "item-001", "item-002"], "profile": profile},
        )
        assert response.status_code == 200
        assert response.json() == expected


def test_aggregate_reports_the_first_missing_id_in_canonical_order(client: TestClient) -> None:
    response = client.post(
        "/aggregate",
        json={"ids": ["item-999", "item-998", "item-001"], "profile": "concurrent"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["details"] == [{"field": "id", "reason": "not_found:item-998"}]


def test_aggregate_rejects_duplicate_ids(client: TestClient) -> None:
    response = client.post(
        "/aggregate",
        json={"ids": ["item-001", "item-001"], "profile": "sequential"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "ids[1]", "reason": "duplicate"}]


def test_aggregate_rejects_unsupported_profile_before_io(client: TestClient) -> None:
    response = client.post(
        "/aggregate",
        json={"ids": ["item-001"], "profile": "parallel"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"] == [
        {"field": "profile", "reason": "supported_values:sequential,concurrent"}
    ]


def test_benchmark_run_id_header_accepts_runner_identifiers(client: TestClient) -> None:
    for run_id in ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", "arm64-512-health-20260101T000000Z"):
        response = client.get("/health", headers={"x-benchmark-run-id": run_id})

        assert response.status_code == 200, run_id


def test_invalid_benchmark_run_id_header_is_rejected(client: TestClient) -> None:
    response = client.get("/health", headers={"x-benchmark-run-id": "-not a run id"})

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"field": "headers.X-Benchmark-Run-Id", "reason": "pattern"}
    ]


class FailingRepository:
    async def get_item(self, item_id: str) -> None:
        raise RepositoryError(f"failed for {item_id}")


def test_repository_failure_uses_the_dependency_error_schema() -> None:
    with TestClient(create_app(FailingRepository())) as failing_client:
        response = failing_client.get("/items/item-001")

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "DEPENDENCY_ERROR",
            "message": "dependency error",
            "details": [],
        }
    }
