//! Rust APIが成功・入力不正・未登録・依存先障害の公開契約を満たすことを検証する。

use std::sync::Arc;

use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode, header},
};
use http_body_util::BodyExt;
use rust_lambdalith::{AppState, build_router, model::Item, repository::InMemoryItemRepository};
use serde_json::{Value, json};
use tower::ServiceExt;

/// `spec/testcases/aggregate.json`と同じ項目を使い、期待チェックサムをPython実装と共有する。
fn app() -> Router {
    let repository = InMemoryItemRepository::new([
        Item {
            id: "item-001".to_owned(),
            value: 123,
            payload: "payload-001".to_owned(),
        },
        Item {
            id: "item-002".to_owned(),
            value: 123,
            payload: "payload-002".to_owned(),
        },
        Item {
            id: "item-003".to_owned(),
            value: 123,
            payload: "payload-003".to_owned(),
        },
    ]);
    build_router(AppState {
        repository: Arc::new(repository),
    })
}

async fn send(request: Request<Body>) -> (StatusCode, Value) {
    let response = app().oneshot(request).await.unwrap();
    let status = response.status();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&body).unwrap())
}

fn get(path: &str) -> Request<Body> {
    Request::builder().uri(path).body(Body::empty()).unwrap()
}

fn post(path: &str, body: Value) -> Request<Body> {
    raw_post(path, body.to_string())
}

fn raw_post(path: &str, body: impl Into<Body>) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(path)
        .header(header::CONTENT_TYPE, "application/json")
        .body(body.into())
        .unwrap()
}

#[tokio::test]
async fn health_returns_the_contract_response() {
    let (status, body) = send(get("/health")).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok"}));
}

#[tokio::test]
async fn transform_normalizes_and_orders_records() {
    let (status, body) = send(post(
        "/transform",
        json!({
            "records": [
                {"id": "item-002", "name": "SECOND", "tags": ["z", "a"], "value": -7},
                {"id": "item-001", "name": "  Example Name  ", "tags": ["b", "a", "b"], "value": 123}
            ]
        }),
    ))
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body,
        json!({
            "records": [
                {"id": "item-001", "name": "example name", "tags": ["a", "b"], "value": 123},
                {"id": "item-002", "name": "second", "tags": ["a", "z"], "value": -7}
            ],
            "count": 2
        })
    );
}

#[tokio::test]
async fn validation_details_use_the_canonical_vocabulary() {
    let (status, body) = send(post(
        "/transform",
        json!({"records": [{"name": "Example", "tags": [], "value": "123", "unexpected": true}]}),
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body,
        json!({
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "request validation failed",
                "details": [
                    {"field": "records[0].id", "reason": "required"},
                    {"field": "records[0].unexpected", "reason": "additional_property_not_allowed"},
                    {"field": "records[0].value", "reason": "type:integer"}
                ]
            }
        })
    );
}

#[tokio::test]
async fn transform_rejects_empty_records() {
    let (status, body) = send(post("/transform", json!({"records": []}))).await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "records", "reason": "min_items:1"}])
    );
}

#[tokio::test]
async fn transform_rejects_duplicate_record_ids() {
    let (status, body) = send(post(
        "/transform",
        json!({
            "records": [
                {"id": "item-001", "name": "First", "tags": [], "value": 1},
                {"id": "item-001", "name": "Second", "tags": [], "value": 2}
            ]
        }),
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "records[1].id", "reason": "duplicate"}])
    );
}

#[tokio::test]
async fn malformed_json_reports_only_the_root_field() {
    let (status, body) = send(raw_post("/compute", r#"{"items":"#)).await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body,
        json!({
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "request validation failed",
                "details": [{"field": "$", "reason": "invalid_json"}]
            }
        })
    );
}

#[tokio::test]
async fn duplicate_object_members_are_invalid_json() {
    let (status, body) = send(raw_post(
        "/compute",
        r#"{"items": 1, "items": 2, "iterations": 1, "seed": "s", "profile": "sequential"}"#,
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "$", "reason": "invalid_json"}])
    );
}

#[tokio::test]
async fn compute_returns_the_canonical_digest() {
    let (status, body) = send(post(
        "/compute",
        json!({"items": 3, "iterations": 2, "seed": "benchmark-seed", "profile": "sequential"}),
    ))
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body,
        json!({
            "processed": 3,
            "iterations": 2,
            "checksum": "4239a9c91e5b6190e724e15b64ba68ebc5d8afa877c2cfb37bee0bb539be4b47"
        })
    );
}

#[tokio::test]
async fn compute_does_not_normalize_utf8_seeds() {
    let (status, body) = send(post(
        "/compute",
        json!({"items": 2, "iterations": 3, "seed": "e\u{301}", "profile": "sequential"}),
    ))
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["checksum"],
        "a275dc01e31c0b02098fbf15ffb92b7272fa88e9c3ca6efc97c178c7102b34eb"
    );
}

#[tokio::test]
async fn compute_rejects_zero_items() {
    let (status, body) = send(post(
        "/compute",
        json!({"items": 0, "iterations": 2, "seed": "benchmark-seed", "profile": "sequential"}),
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "items", "reason": "minimum:1"}])
    );
}

#[tokio::test]
async fn compute_rejects_work_over_the_limit() {
    let (status, body) = send(post(
        "/compute",
        json!({
            "items": 1000,
            "iterations": 1001,
            "seed": "benchmark-seed",
            "profile": "sequential"
        }),
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "$", "reason": "maximum_work:1000000"}])
    );
}

#[tokio::test]
async fn compute_rejects_a_noncanonical_profile() {
    let (status, body) = send(post(
        "/compute",
        json!({"items": 1, "iterations": 1, "seed": "seed", "profile": "parallel"}),
    ))
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body,
        json!({
            "error": {
                "code": "UNSUPPORTED_PROFILE",
                "message": "profile is not supported",
                "details": [{"field": "profile", "reason": "supported_values:sequential"}]
            }
        })
    );
}

#[tokio::test]
async fn item_returns_the_stored_item() {
    let (status, body) = send(get("/items/item-001")).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body,
        json!({"item": {"id": "item-001", "value": 123, "payload": "payload-001"}})
    );
}

#[tokio::test]
async fn item_returns_the_canonical_not_found_error() {
    let (status, body) = send(get("/items/item-999")).await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(
        body,
        json!({
            "error": {
                "code": "NOT_FOUND",
                "message": "item not found",
                "details": [{"field": "id", "reason": "not_found:item-999"}]
            }
        })
    );
}

#[tokio::test]
async fn item_rejects_ids_outside_the_contract_pattern() {
    let (status, body) = send(get("/items/-invalid")).await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "id", "reason": "pattern"}])
    );
}

#[tokio::test]
async fn aggregate_profiles_return_the_same_id_ordered_result() {
    for profile in ["sequential", "concurrent"] {
        let (status, body) = send(post(
            "/aggregate",
            json!({"ids": ["item-003", "item-001", "item-002"], "profile": profile}),
        ))
        .await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            body,
            json!({
                "count": 3,
                "total_value": 369,
                "checksum": "eed0d10cad0b91b57d98173575bde1b1106a9453fcf5c5b1347051c74e7892f3"
            })
        );
    }
}

#[tokio::test]
async fn aggregate_reports_the_first_missing_id_in_canonical_order() {
    for profile in ["sequential", "concurrent"] {
        let (status, body) = send(post(
            "/aggregate",
            json!({"ids": ["item-999", "item-998", "item-001"], "profile": profile}),
        ))
        .await;

        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(
            body["error"]["details"],
            json!([{"field": "id", "reason": "not_found:item-998"}])
        );
    }
}

#[tokio::test]
async fn aggregate_rejects_duplicate_ids() {
    let (status, body) = send(post(
        "/aggregate",
        json!({"ids": ["item-001", "item-001"], "profile": "sequential"}),
    ))
    .await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "ids[1]", "reason": "duplicate"}])
    );
}

#[tokio::test]
async fn aggregate_rejects_an_unsupported_profile_before_io() {
    let (status, body) = send(post(
        "/aggregate",
        json!({"ids": ["item-001"], "profile": "parallel"}),
    ))
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "profile", "reason": "supported_values:sequential,concurrent"}])
    );
}

#[tokio::test]
async fn the_benchmark_run_id_header_accepts_runner_identifiers() {
    for run_id in [
        "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "arm64-512-health-20260101T000000Z",
    ] {
        let request = Request::builder()
            .uri("/health")
            .header("x-benchmark-run-id", run_id)
            .body(Body::empty())
            .unwrap();
        let (status, _) = send(request).await;

        assert_eq!(status, StatusCode::OK, "{run_id}");
    }
}

#[tokio::test]
async fn an_invalid_benchmark_run_id_header_is_rejected() {
    let request = Request::builder()
        .uri("/health")
        .header("x-benchmark-run-id", "-not a run id")
        .body(Body::empty())
        .unwrap();
    let (status, body) = send(request).await;

    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"],
        json!([{"field": "headers.X-Benchmark-Run-Id", "reason": "pattern"}])
    );
}
