//! 共有する正規テストベクトルをRust実装へ適用する。
//!
//! `spec/testcases`を契約の正本とし、Python実装も同じファイルを使うことで、デプロイ環境なしに
//! 両実装の応答を比較する。

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

/// ケースの本文を送信するバイト列へ変換する。生の本文は組み立て直さずにそのまま送る。
///
/// raw_body・raw_body_base64は、JSONとして不正な本文や数値の表記をそのまま送るために使う。
fn request_body(request: &Value) -> Option<Vec<u8>> {
    if let Some(encoded) = request["raw_body_base64"].as_str() {
        return Some(decode_base64(encoded));
    }
    if let Some(raw) = request["raw_body"].as_str() {
        return Some(raw.as_bytes().to_vec());
    }
    let body = &request["body"];
    (!body.is_null()).then(|| body.to_string().into_bytes())
}

/// テストケースの短いbase64（パディング付きの標準形式）だけを読む。依存を増やさないための最小実装。
fn decode_base64(encoded: &str) -> Vec<u8> {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = Vec::new();
    let mut buffer = 0u32;
    let mut bits = 0;
    for byte in encoded.bytes().filter(|byte| *byte != b'=') {
        let value = ALPHABET
            .iter()
            .position(|candidate| *candidate == byte)
            .expect("raw_body_base64 must be standard base64") as u32;
        buffer = (buffer << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            output.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    output
}

#[tokio::test]
async fn every_canonical_case_matches() {
    let documents = testcase_documents();
    let repository = InMemoryItemRepository::new(fixture_items(&documents));
    let state = AppState {
        repository: Arc::new(repository),
    };

    let mut executed = 0;
    let mut mismatches = Vec::new();
    for document in &documents {
        // 一部の言語だけで実装済みのケースファイルは、Rustが対象に含まれる場合だけ使う。
        if let Some(implementations) = document["implementations"].as_array()
            && !implementations.iter().any(|key| key == "rust")
        {
            continue;
        }
        let cases = document["cases"]
            .as_array()
            .expect("cases must be an array");
        for case in cases {
            let name = case["name"].as_str().unwrap_or("unnamed");
            let request = &case["request"];
            let expected = &case["expected"];

            let method = request["method"].as_str().unwrap();
            let path = request["path"].as_str().unwrap();

            let mut builder = Request::builder().method(method).uri(path);
            let http_request = match request_body(request) {
                None => builder.body(Body::empty()).unwrap(),
                Some(bytes) => {
                    builder = builder.header(header::CONTENT_TYPE, "application/json");
                    builder.body(Body::from(bytes)).unwrap()
                }
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
            // 一つの不一致で止めず、全ケースの不一致をまとめて報告する。
            if status != expected_status || actual != expected["body"] {
                mismatches.push(format!(
                    "{name}: expected {expected_status} {}, got {status} {actual}",
                    expected["body"]
                ));
            }
            executed += 1;
        }
    }

    assert!(
        mismatches.is_empty(),
        "contract mismatches:\n{}",
        mismatches.join("\n")
    );
    assert!(
        executed >= 12,
        "expected the full canonical suite, ran {executed}"
    );
}
