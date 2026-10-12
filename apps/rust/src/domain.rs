//! 外部I/Oを含まない変換・計算・集約処理を定義する。
//!
//! 同じ入力に対してPython実装と同じ出力を返すため、並び順、ハッシュ入力、未登録IDの
//! 選び方を決定的にする。

use std::{collections::BTreeSet, sync::Arc};

use sha2::{Digest, Sha256};
use tokio::task::JoinSet;

use crate::{
    error::AppError,
    model::{
        AggregateResponse, ComputeRequest, ComputeResponse, Item, TransformRequest,
        TransformResponse, TransformedRecord,
    },
    repository::ItemRepository,
};

pub fn transform(request: TransformRequest) -> TransformResponse {
    // 受け付けた各レコードを一件ずつ加工し、APIレスポンス用のレコードへ変換する。
    let mut records = request
        .records
        .into_iter()
        .map(|record| TransformedRecord {
            id: record.id,

            // 一般的な空白ではなくU+0020 SPACEだけを取り除き、名前を小文字へ正規化する。
            name: record.name.trim_matches(' ').to_lowercase(),

            // タグの重複を除去し、入力順に依存しない昇順の一覧にする。
            tags: record
                .tags
                .into_iter()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            value: record.value,
        })
        .collect::<Vec<_>>();

    // 入力順に関係なく同じレスポンスを返すため、全レコードをID昇順に統一する。
    records.sort_by(|left, right| left.id.cmp(&right.id));

    // 正規化済みのレコード数と一覧をレスポンスとして返す。
    TransformResponse {
        count: records.len(),
        records,
    }
}

pub fn compute(request: &ComputeRequest) -> ComputeResponse {
    // 全項目のダイジェストを最後にまとめてハッシュ化するための集約器を用意する。
    let mut final_hasher = Sha256::new();

    // 項目数の範囲で、seedと添字から独立した初期値を作り、指定回数だけSHA-256を適用する。
    for index in 0..request.items {
        let initial = format!("{}:{index}", request.seed);
        let mut digest = Sha256::digest(initial.as_bytes());
        for _ in 1..request.iterations {
            digest = Sha256::digest(digest);
        }
        final_hasher.update(digest);
    }

    ComputeResponse {
        processed: request.items,
        iterations: request.iterations,
        checksum: format!("{:x}", final_hasher.finalize()),
    }
}

pub async fn get_item(repository: &dyn ItemRepository, id: &str) -> Result<Item, AppError> {
    // 保存先の呼び出しが失敗した場合は502、成功しても項目がない場合は404へ区別して変換する。
    repository
        .get(id)
        .await
        .map_err(|_| AppError::dependency())?
        .ok_or_else(|| AppError::not_found(id))
}

pub async fn aggregate_sequential(
    repository: &dyn ItemRepository,
    ids: &[String],
) -> Result<AggregateResponse, AppError> {
    // 依頼されたID数に見合う容量を確保し、ID順に一件ずつ取得した結果を蓄積する。
    let mut found = Vec::with_capacity(ids.len());
    let mut missing = Vec::new();

    for id in ids {
        // 保存先が項目を返した場合は集約候補へ、未登録の場合は後で404を決める候補へ分ける。
        match repository
            .get(id)
            .await
            .map_err(|_| AppError::dependency())?
        {
            Some(item) => found.push(item),
            None => missing.push(id.clone()),
        }
    }

    aggregate(found, missing)
}

pub async fn aggregate_concurrent(
    repository: Arc<dyn ItemRepository>,
    ids: Vec<String>,
) -> Result<AggregateResponse, AppError> {
    // 各IDの読み取りを独立タスクとして開始し、直列方式と同じくIDごとに一回だけ外部I/Oを行う。
    let mut reads = JoinSet::new();
    for id in ids {
        let repository = Arc::clone(&repository);
        reads.spawn(async move {
            let result = repository.get(&id).await;
            (id, result)
        });
    }

    let mut found = Vec::with_capacity(reads.len());
    let mut missing = Vec::new();

    // 完了順は不定でも、取得結果を全件回収してから決定的な集約処理へ渡す。
    while let Some(read) = reads.join_next().await {
        let (id, result) = read.map_err(|_| AppError::internal())?;

        // 保存先が項目を返した場合は集約候補へ、未登録の場合は404候補へ分ける。
        match result.map_err(|_| AppError::dependency())? {
            Some(item) => found.push(item),
            None => missing.push(id),
        }
    }

    aggregate(found, missing)
}

/// 読み取り結果を、実行方式に依存しない共通の集約結果へ正規化する。
///
/// 未登録IDは完了順ではなく辞書順で選び、直列・並行のどちらでも同じ失敗応答にする。
fn aggregate(mut items: Vec<Item>, missing: Vec<String>) -> Result<AggregateResponse, AppError> {
    // 未登録IDが一件以上ある場合は、辞書順で最初のIDを404として返し、並行処理の完了順を公開しない。
    if let Some(first_missing) = missing.iter().min() {
        return Err(AppError::not_found(first_missing));
    }

    // 全項目が存在する場合だけ、ID順を固定して値の合計とpayload連結順を決定する。
    items.sort_by(|left, right| left.id.cmp(&right.id));

    let mut total_value = 0_i64;
    let mut checksum = Sha256::new();
    for item in &items {
        // 加算結果がi64の範囲を超える場合は、誤った合計を返さず内部エラーとして処理を止める。
        total_value = total_value
            .checked_add(item.value)
            .ok_or_else(AppError::internal)?;
        checksum.update(item.payload.as_bytes());
    }

    Ok(AggregateResponse {
        count: items.len(),
        total_value,
        checksum: format!("{:x}", checksum.finalize()),
    })
}

#[cfg(test)]
mod tests {
    use crate::model::TransformRecord;

    use super::*;

    #[test]
    fn transform_normalizes_and_orders_records() {
        let response = transform(TransformRequest {
            records: vec![
                TransformRecord {
                    id: "item-002".to_owned(),
                    name: "  SECOND ".to_owned(),
                    tags: vec!["b".to_owned(), "a".to_owned(), "b".to_owned()],
                    value: 2,
                },
                TransformRecord {
                    id: "item-001".to_owned(),
                    name: " Example Name ".to_owned(),
                    tags: vec![],
                    value: 1,
                },
            ],
        });

        assert_eq!(response.count, 2);
        assert_eq!(response.records[0].id, "item-001");
        assert_eq!(response.records[0].name, "example name");
        assert_eq!(response.records[1].tags, ["a", "b"]);
    }

    #[test]
    fn compute_uses_raw_digest_as_the_next_iteration_input() {
        let response = compute(&ComputeRequest {
            items: 2,
            iterations: 2,
            seed: "seed".to_owned(),
            profile: "sequential".to_owned(),
        });

        assert_eq!(
            response.checksum,
            "247b9b26a1a5710446aef395ce9b133cf691fae27f50d1c2dcd639ea7aa6193f"
        );
    }

    #[test]
    fn aggregate_orders_by_id_before_hashing_payloads() {
        let response = aggregate(
            vec![
                Item {
                    id: "item-002".to_owned(),
                    value: 2,
                    payload: "B".to_owned(),
                },
                Item {
                    id: "item-001".to_owned(),
                    value: 1,
                    payload: "A".to_owned(),
                },
            ],
            Vec::new(),
        )
        .unwrap();

        assert_eq!(response.count, 2);
        assert_eq!(response.total_value, 3);
        assert_eq!(
            response.checksum,
            "38164fbd17603d73f696b8b4d72664d735bb6a7c88577687fd2ae33fd6964153"
        );
    }

    #[test]
    fn aggregate_reports_the_canonically_first_missing_id() {
        let error = aggregate(
            Vec::new(),
            vec!["item-999".to_owned(), "item-998".to_owned()],
        )
        .unwrap_err();

        assert_eq!(error.status(), axum::http::StatusCode::NOT_FOUND);
        assert_eq!(error.details()[0].reason, "not_found:item-998");
    }
}
