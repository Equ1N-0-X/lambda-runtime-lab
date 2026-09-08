//! Runs the shared canonical test vectors against the Rust implementation.
//!
//! `spec/testcases` is the single source of truth for the contract. The Python
//! implementation runs the same files, which is what keeps the two comparable
//! without a deployed environment.

use std::{fs, path::PathBuf, sync::Arc};

use axum::{
    body::Body,
    http::{Request, StatusCode, header},
};
use http_body_util::BodyExt;
use rust_lambdalith::{AppState, build_router, model::Item, repository::InMemoryItemRepository};
use serde_json::Value;
use tower::ServiceExt;

fn testcase_documents() -> Vec<Value> {
    let directory = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../spec/testcases");
    let mut paths: Vec<PathBuf> = fs::read_dir(&directory)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", directory.display()))
        .map(|entry| entry.unwrap().path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "json")
        })
        .collect();
    paths.sort();
    assert!(!paths.is_empty(), "no canonical test cases found");

    paths
        .iter()
        .map(|path| {
            let text = fs::read_to_string(path).unwrap();
            serde_json::from_str(&text)
                .unwrap_or_else(|error| panic!("{}: {error}", path.display()))
        })
        .collect()
}

fn fixture_items(documents: &[Value]) -> Vec<Item> {
    let mut items: Vec<Item> = Vec::new();
    for document in documents {
        let Some(fixtures) = document["fixtures"]["items"].as_array() else {
            continue;
        };
        for fixture in fixtures {
            let id = fixture["id"].as_str().unwrap().to_owned();
            if items.iter().any(|item| item.id == id) {
                continue;
            }
            items.push(Item {
                id,
                value: fixture["value"].as_i64().unwrap(),
                payload: fixture["payload"].as_str().unwrap().to_owned(),
            });
        }
    }
    items
}

#[tokio::test]
async fn every_canonical_case_matches() {
    let documents = testcase_documents();
    let repository = InMemoryItemRepository::new(fixture_items(&documents));
    let state = AppState {
        repository: Arc::new(repository),
    };

    let mut executed = 0;
    for document in &documents {
        let cases = document["cases"]
            .as_array()
            .expect("cases must be an array");
        for case in cases {
            let name = case["name"].as_str().unwrap_or("unnamed");
            let request = &case["request"];
            let expected = &case["expected"];

            let method = request["method"].as_str().unwrap();
            let path = request["path"].as_str().unwrap();
            let body = &request["body"];

            let mut builder = Request::builder().method(method).uri(path);
            let http_request = if body.is_null() {
                builder.body(Body::empty()).unwrap()
            } else {
                builder = builder.header(header::CONTENT_TYPE, "application/json");
                builder.body(Body::from(body.to_string())).unwrap()
            };

            let response = build_router(state.clone())
                .oneshot(http_request)
                .await
                .unwrap();
            let status = response.status();
            let payload = response.into_body().collect().await.unwrap().to_bytes();
            let actual: Value = serde_json::from_slice(&payload)
                .unwrap_or_else(|error| panic!("{name}: non-JSON response: {error}"));

            let expected_status =
                StatusCode::from_u16(expected["status"].as_u64().unwrap() as u16).unwrap();
            assert_eq!(status, expected_status, "{name}: unexpected status");
            assert_eq!(actual, expected["body"], "{name}: unexpected body");
            executed += 1;
        }
    }

    assert!(
        executed >= 12,
        "expected the full canonical suite, ran {executed}"
    );
}
