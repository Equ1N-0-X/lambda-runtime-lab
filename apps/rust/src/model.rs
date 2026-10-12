//! OpenAPI契約に対応するリクエスト・レスポンスのデータ構造を定義する。
//!
//! リクエストはデコード済みJSONから検証層が組み立てる。外部へ送るレスポンスだけを
//! `Serialize` 対象にし、未検証の入力構造を誤って返さない。

use serde::Serialize;

/// 検証済みの変換対象レコード一覧。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransformRequest {
    pub records: Vec<TransformRecord>,
}

/// ID・名前・タグ・数値を持つ変換前の一レコード。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransformRecord {
    pub id: String,
    pub name: String,
    pub tags: Vec<String>,
    pub value: i64,
}

/// 正規化・ID整列済みレコードと、その件数。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct TransformResponse {
    pub records: Vec<TransformedRecord>,
    pub count: usize,
}

/// 名前・タグを正規化済みの一レコード。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct TransformedRecord {
    pub id: String,
    pub name: String,
    pub tags: Vec<String>,
    pub value: i64,
}

/// 項目数、反復回数、seed、実行方式からなるCPU計算条件。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ComputeRequest {
    pub items: i64,
    pub iterations: i64,
    pub seed: String,
    pub profile: String,
}

/// 実際の処理条件と最終チェックサム。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ComputeResponse {
    pub processed: i64,
    pub iterations: i64,
    pub checksum: String,
}

/// DynamoDBで共有するID、集計値、固定長payload。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Item {
    pub id: String,
    pub value: i64,
    pub payload: String,
}

/// ID指定で取得した一項目の応答。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ItemResponse {
    pub item: Item,
}

/// 重複しないID一覧と、直列・並行の読み取り方式。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AggregateRequest {
    pub ids: Vec<String>,
    pub profile: String,
}

/// ID順で集約した件数、合計値、payloadチェックサム。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct AggregateResponse {
    pub count: usize,
    pub total_value: i64,
    pub checksum: String,
}

/// 外部依存へアクセスせず、Lambdaが処理可能であることを示す固定応答。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
}
