//! Request and response shapes.
//!
//! Requests are built by [`crate::validation`] from a decoded JSON document,
//! so only responses derive `Serialize`.

use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransformRequest {
    pub records: Vec<TransformRecord>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransformRecord {
    pub id: String,
    pub name: String,
    pub tags: Vec<String>,
    pub value: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct TransformResponse {
    pub records: Vec<TransformedRecord>,
    pub count: usize,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct TransformedRecord {
    pub id: String,
    pub name: String,
    pub tags: Vec<String>,
    pub value: i64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ComputeRequest {
    pub items: i64,
    pub iterations: i64,
    pub seed: String,
    pub profile: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ComputeResponse {
    pub processed: i64,
    pub iterations: i64,
    pub checksum: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Item {
    pub id: String,
    pub value: i64,
    pub payload: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ItemResponse {
    pub item: Item,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AggregateRequest {
    pub ids: Vec<String>,
    pub profile: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AggregateResponse {
    pub count: usize,
    pub total_value: i64,
    pub checksum: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
}
