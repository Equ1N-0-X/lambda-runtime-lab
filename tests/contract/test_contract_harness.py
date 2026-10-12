"""契約ケースの読込、正規化、fixture統合、応答比較というハーネス自身の規則を検証する。"""

import json
from pathlib import Path

import pytest

from contract_harness import (
    ApiResponse,
    assert_case_matches,
    load_cases,
    load_fixture_items,
    normalized_json,
)


def test_load_cases_uses_canonical_request_and_response(tmp_path: Path) -> None:
    case_file = tmp_path / "health.json"
    case_file.write_text(
        json.dumps(
            {
                "name": "health",
                "request": {"method": "GET", "path": "/health"},
                "expected_response": {
                    "status": 200,
                    "body": {"status": "ok"},
                },
            }
        ),
        encoding="utf-8",
    )

    cases = load_cases(tmp_path)

    assert len(cases) == 1
    assert cases[0].method == "GET"
    assert cases[0].path == "/health"
    assert cases[0].expected_body == {"status": "ok"}


def test_normalized_json_preserves_array_order_and_number_types() -> None:
    assert normalized_json({"values": [1, 2]}, ()) != normalized_json({"values": [2, 1]}, ())
    assert normalized_json({"value": 1}, ()) != normalized_json({"value": 1.0}, ())


def test_assert_case_matches_removes_only_declared_nondeterministic_fields(
    tmp_path: Path,
) -> None:
    case_file = tmp_path / "response.json"
    case_file.write_text(
        json.dumps(
            {
                "request": {"method": "GET", "path": "/health"},
                "expected": {
                    "status_code": 200,
                    "json": {"status": "ok", "request_id": "canonical"},
                },
                "ignored_response_fields": ["/request_id"],
            }
        ),
        encoding="utf-8",
    )
    case = load_cases(tmp_path)[0]

    assert_case_matches(
        case,
        ApiResponse(200, {"status": "ok", "request_id": "python"}),
        ApiResponse(200, {"status": "ok", "request_id": "rust"}),
    )


def test_load_cases_rejects_missing_canonical_response(tmp_path: Path) -> None:
    (tmp_path / "invalid.json").write_text(
        json.dumps({"request": {"method": "GET", "path": "/health"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_response"):
        load_cases(tmp_path)


def test_load_cases_flattens_canonical_case_files(tmp_path: Path) -> None:
    (tmp_path / "compute.json").write_text(
        json.dumps(
            {
                "contract_version": "1.0.0",
                "cases": [
                    {
                        "name": "first",
                        "request": {"method": "GET", "path": "/health"},
                        "expected": {"status": 200, "body": {"status": "ok"}},
                    },
                    {
                        "name": "second",
                        "request": {"method": "GET", "path": "/health"},
                        "expected": {"status": 200, "body": {"status": "ok"}},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert [case.name for case in load_cases(tmp_path)] == ["first", "second"]


def test_load_fixture_items_merges_identical_items(tmp_path: Path) -> None:
    fixture = {
        "fixtures": {"items": [{"id": "item-001", "value": 123, "payload": "payload-001"}]},
        "cases": [
            {
                "request": {"method": "GET", "path": "/health"},
                "expected": {"status": 200, "body": {"status": "ok"}},
            }
        ],
    }
    (tmp_path / "one.json").write_text(json.dumps(fixture), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(fixture), encoding="utf-8")

    assert load_fixture_items(tmp_path) == [
        {"id": "item-001", "value": 123, "payload": "payload-001"}
    ]
