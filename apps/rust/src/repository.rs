//! DynamoDBとインメモリ保存先を、共通の項目取得境界として提供する。

use std::{collections::HashMap, sync::Arc};

use async_trait::async_trait;
use aws_sdk_dynamodb::{Client, error::ProvideErrorMetadata, types::AttributeValue};
use thiserror::Error;

use crate::model::Item;

/// 保存先の障害の原因の分類。調査用の内部ログにだけ残し、公開する応答には含めない。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DependencyCategory {
    Permission,
    Table,
    Throttling,
    Network,
    InvalidItem,
    Unknown,
}

impl DependencyCategory {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Permission => "permission",
            Self::Table => "table",
            Self::Throttling => "throttling",
            Self::Network => "network",
            Self::InvalidItem => "invalid_item",
            Self::Unknown => "unknown",
        }
    }
}

/// 保存先の通信失敗または保存データ不正を、原因の分類とともに伝える。
#[derive(Debug, Error)]
#[error("DynamoDBの項目を取得できませんでした（{}）", category.as_str())]
pub struct RepositoryError {
    pub category: DependencyCategory,
    /// AWSが返したエラーコード。通信の失敗や保存値の不正ではNone。
    pub error_code: Option<String>,
}

impl RepositoryError {
    pub fn new(category: DependencyCategory, error_code: Option<String>) -> Self {
        Self {
            category,
            error_code,
        }
    }

    fn invalid_item() -> Self {
        Self::new(DependencyCategory::InvalidItem, None)
    }
}

const PERMISSION_CODES: [&str; 4] = [
    "AccessDeniedException",
    "UnrecognizedClientException",
    "ExpiredTokenException",
    "InvalidSignatureException",
];
const THROTTLING_CODES: [&str; 3] = [
    "ProvisionedThroughputExceededException",
    "ThrottlingException",
    "RequestLimitExceeded",
];
const MIN_ITEM_VALUE: i64 = -1_000_000_000;
const MAX_ITEM_VALUE: i64 = 1_000_000_000;
const MAX_PAYLOAD_CHARS: usize = 4096;

/// AWSのエラーコードから、権限・テーブル・流量制限・その他の原因に分類する。
/// コードがない（AWSが応答していない）場合は、通信の問題として扱う。
pub fn classify_error_code(code: Option<&str>) -> RepositoryError {
    let category = match code {
        None => DependencyCategory::Network,
        Some(code) if PERMISSION_CODES.contains(&code) => DependencyCategory::Permission,
        Some("ResourceNotFoundException") => DependencyCategory::Table,
        Some(code) if THROTTLING_CODES.contains(&code) => DependencyCategory::Throttling,
        Some(_) => DependencyCategory::Unknown,
    };
    RepositoryError::new(category, code.map(str::to_owned))
}

#[async_trait]
pub trait ItemRepository: Send + Sync {
    /// IDで一件を取得し、未登録はNone、保存先障害はRepositoryErrorとして返す。
    async fn get(&self, id: &str) -> Result<Option<Item>, RepositoryError>;
}

pub struct DynamoDbItemRepository {
    client: Client,
    table_name: String,
}

impl DynamoDbItemRepository {
    /// AWS SDKクライアントと対象テーブル名を保持するDynamoDB実装を作る。
    pub fn new(client: Client, table_name: impl Into<String>) -> Self {
        Self {
            client,
            table_name: table_name.into(),
        }
    }
}

#[async_trait]
impl ItemRepository for DynamoDbItemRepository {
    async fn get(&self, id: &str) -> Result<Option<Item>, RepositoryError> {
        // 強整合読み取りを一回実行し、SDKエラーは利用者入力と区別して依存先障害に変換する。
        let output = self
            .client
            .get_item()
            .table_name(&self.table_name)
            .key("id", AttributeValue::S(id.to_owned()))
            .consistent_read(true)
            .send()
            .await
            .map_err(|error| classify_error_code(error.code()))?;

        output.item.map(parse_item).transpose()
    }
}

/// DynamoDBの属性を公開するItemへ変換する。
///
/// 公開するItemの境界（IDの形式・値の範囲・payloadの長さ）を満たさない保存値は、
/// 成功の応答にせず保存データの不正とする。
fn parse_item(attributes: HashMap<String, AttributeValue>) -> Result<Item, RepositoryError> {
    let id = string_attribute(&attributes, "id")?;
    let value = string_number_attribute(&attributes, "value")?;
    let payload = string_attribute(&attributes, "payload")?;

    // IDの形式、値の範囲、payloadの長さのいずれかが契約外なら、保存データの不正とする。
    if !is_item_id(&id)
        || !(MIN_ITEM_VALUE..=MAX_ITEM_VALUE).contains(&value)
        || payload.chars().count() > MAX_PAYLOAD_CHARS
    {
        return Err(RepositoryError::invalid_item());
    }
    Ok(Item { id, value, payload })
}

/// 正本のItemIdの形式（英数字で始まる、英数字と . _ - の1〜128文字）か。
fn is_item_id(id: &str) -> bool {
    let mut characters = id.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphanumeric())
        && id.len() <= 128
        && characters.all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
}

fn string_attribute(
    attributes: &HashMap<String, AttributeValue>,
    name: &str,
) -> Result<String, RepositoryError> {
    attributes
        .get(name)
        .and_then(|value| value.as_s().ok())
        .cloned()
        .ok_or_else(RepositoryError::invalid_item)
}

fn string_number_attribute(
    attributes: &HashMap<String, AttributeValue>,
    name: &str,
) -> Result<i64, RepositoryError> {
    attributes
        .get(name)
        .and_then(|value| value.as_n().ok())
        // 保存値の数値は、DynamoDBが正規化して返す10進整数の表記だけを受け付ける（全言語で共通）。
        .filter(|value| {
            let digits = value.strip_prefix('-').unwrap_or(value);
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
        .and_then(|value| value.parse().ok())
        .ok_or_else(RepositoryError::invalid_item)
}

#[derive(Clone, Default)]
pub struct InMemoryItemRepository {
    items: Arc<HashMap<String, Item>>,
}

impl InMemoryItemRepository {
    /// テストで外部I/Oなしに同じ取得契約を再現する項目集合を作る。
    pub fn new(items: impl IntoIterator<Item = Item>) -> Self {
        Self {
            items: Arc::new(
                items
                    .into_iter()
                    .map(|item| (item.id.clone(), item))
                    .collect(),
            ),
        }
    }
}

#[async_trait]
impl ItemRepository for InMemoryItemRepository {
    async fn get(&self, id: &str) -> Result<Option<Item>, RepositoryError> {
        Ok(self.items.get(id).cloned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn in_memory_repository_returns_a_cloned_item() {
        let repository = InMemoryItemRepository::new([Item {
            id: "item-001".to_owned(),
            value: 123,
            payload: "payload".to_owned(),
        }]);

        assert_eq!(
            repository.get("item-001").await.unwrap(),
            Some(Item {
                id: "item-001".to_owned(),
                value: 123,
                payload: "payload".to_owned(),
            })
        );
        assert_eq!(repository.get("missing").await.unwrap(), None);
    }
}

#[cfg(test)]
mod stored_item_tests {
    use serde_json::Value;

    use super::*;

    /// 全言語で共有する保存値のケースを、DynamoDBの属性へ変換して読む。
    fn stored_item_cases() -> Vec<Value> {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../spec/repository-cases/stored-items.json"
        );
        let document: Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        document["cases"].as_array().unwrap().clone()
    }

    fn attributes(value: &Value) -> HashMap<String, AttributeValue> {
        value
            .as_object()
            .unwrap()
            .iter()
            .map(|(name, attribute)| {
                let converted = match (attribute.get("S"), attribute.get("N")) {
                    (Some(text), _) => AttributeValue::S(text.as_str().unwrap().to_owned()),
                    (_, Some(number)) => AttributeValue::N(number.as_str().unwrap().to_owned()),
                    _ => panic!("unsupported attribute in shared case"),
                };
                (name.clone(), converted)
            })
            .collect()
    }

    #[test]
    fn stored_item_conversion_follows_shared_cases() {
        let mut mismatches = Vec::new();
        for case in stored_item_cases() {
            let name = case["name"].as_str().unwrap();
            let result = parse_item(attributes(&case["attributes"]));
            // 全言語で同じ保存値の境界を使い、契約外の保存値を成功の応答にしない。
            let matches = match (&result, case["expected"].get("item")) {
                (Ok(item), Some(expected)) => {
                    item.id == expected["id"]
                        && item.value == expected["value"].as_i64().unwrap()
                        && item.payload == expected["payload"]
                }
                (Err(error), None) => error.category == DependencyCategory::InvalidItem,
                _ => false,
            };
            if !matches {
                mismatches.push(format!("{name}: {result:?}"));
            }
        }
        assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
    }

    #[test]
    fn sdk_error_codes_are_classified_by_cause() {
        let cases = [
            (
                Some("AccessDeniedException"),
                DependencyCategory::Permission,
            ),
            (Some("ResourceNotFoundException"), DependencyCategory::Table),
            (Some("ThrottlingException"), DependencyCategory::Throttling),
            (Some("InternalServerError"), DependencyCategory::Unknown),
            (None, DependencyCategory::Network),
        ];
        for (code, category) in cases {
            let error = classify_error_code(code);
            assert_eq!(error.category, category, "{code:?}");
            assert_eq!(error.error_code.as_deref(), code);
        }
    }
}
