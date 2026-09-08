use std::{collections::HashMap, sync::Arc};

use async_trait::async_trait;
use aws_sdk_dynamodb::{Client, types::AttributeValue};
use thiserror::Error;

use crate::model::Item;

#[derive(Debug, Error)]
pub enum RepositoryError {
    #[error("DynamoDB request failed")]
    Dependency,
    #[error("DynamoDB item has an invalid schema")]
    InvalidItem,
}

#[async_trait]
pub trait ItemRepository: Send + Sync {
    async fn get(&self, id: &str) -> Result<Option<Item>, RepositoryError>;
}

pub struct DynamoDbItemRepository {
    client: Client,
    table_name: String,
}

impl DynamoDbItemRepository {
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
        let output = self
            .client
            .get_item()
            .table_name(&self.table_name)
            .key("id", AttributeValue::S(id.to_owned()))
            .consistent_read(true)
            .send()
            .await
            .map_err(|_| RepositoryError::Dependency)?;

        output
            .item
            .map(parse_item)
            .transpose()
            .map_err(|_| RepositoryError::InvalidItem)
    }
}

fn parse_item(attributes: HashMap<String, AttributeValue>) -> Result<Item, RepositoryError> {
    let id = string_attribute(&attributes, "id")?;
    let value = string_number_attribute(&attributes, "value")?;
    let payload = string_attribute(&attributes, "payload")?;

    Ok(Item { id, value, payload })
}

fn string_attribute(
    attributes: &HashMap<String, AttributeValue>,
    name: &str,
) -> Result<String, RepositoryError> {
    attributes
        .get(name)
        .and_then(|value| value.as_s().ok())
        .cloned()
        .ok_or(RepositoryError::InvalidItem)
}

fn string_number_attribute(
    attributes: &HashMap<String, AttributeValue>,
    name: &str,
) -> Result<i64, RepositoryError> {
    attributes
        .get(name)
        .and_then(|value| value.as_n().ok())
        .and_then(|value| value.parse().ok())
        .ok_or(RepositoryError::InvalidItem)
}

#[derive(Clone, Default)]
pub struct InMemoryItemRepository {
    items: Arc<HashMap<String, Item>>,
}

impl InMemoryItemRepository {
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
