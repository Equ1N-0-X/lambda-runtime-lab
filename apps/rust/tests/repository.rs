//! インメモリ保存先がDynamoDB実装と同じ一件取得契約を満たすことを検証する。

use rust_lambdalith::{
    model::Item,
    repository::{InMemoryItemRepository, ItemRepository},
};

#[tokio::test]
async fn in_memory_repository_obeys_the_external_io_contract() {
    let repository = InMemoryItemRepository::new([Item {
        id: "item-001".to_owned(),
        value: -7,
        payload: "UTF-8 payload: 日本語".to_owned(),
    }]);

    let item = repository.get("item-001").await.unwrap().unwrap();
    assert_eq!(item.id, "item-001");
    assert_eq!(item.value, -7);
    assert_eq!(item.payload, "UTF-8 payload: 日本語");
    assert!(repository.get("item-002").await.unwrap().is_none());
}
