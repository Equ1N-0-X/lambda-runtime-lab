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
    let mut records = request
        .records
        .into_iter()
        .map(|record| TransformedRecord {
            id: record.id,
            name: record.name.trim_matches(' ').to_lowercase(),
            tags: record
                .tags
                .into_iter()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            value: record.value,
        })
        .collect::<Vec<_>>();
    records.sort_by(|left, right| left.id.cmp(&right.id));

    TransformResponse {
        count: records.len(),
        records,
    }
}

pub fn compute(request: &ComputeRequest) -> ComputeResponse {
    let mut final_hasher = Sha256::new();

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
    let mut found = Vec::with_capacity(ids.len());
    let mut missing = Vec::new();

    for id in ids {
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
    while let Some(read) = reads.join_next().await {
        let (id, result) = read.map_err(|_| AppError::internal())?;
        match result.map_err(|_| AppError::dependency())? {
            Some(item) => found.push(item),
            None => missing.push(id),
        }
    }

    aggregate(found, missing)
}

/// Normalizes a completed set of reads into the canonical aggregate result.
///
/// Missing IDs are reported by canonical order rather than completion order so
/// that the sequential and concurrent profiles fail identically.
fn aggregate(mut items: Vec<Item>, missing: Vec<String>) -> Result<AggregateResponse, AppError> {
    if let Some(first_missing) = missing.iter().min() {
        return Err(AppError::not_found(first_missing));
    }

    items.sort_by(|left, right| left.id.cmp(&right.id));

    let mut total_value = 0_i64;
    let mut checksum = Sha256::new();
    for item in &items {
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
