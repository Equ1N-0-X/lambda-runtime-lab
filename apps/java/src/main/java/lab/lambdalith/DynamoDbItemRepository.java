package lab.lambdalith;

import java.util.Map;
import java.util.Optional;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;

/** DynamoDBから強整合読み取りで一件ずつ取得する保存先。 */
public final class DynamoDbItemRepository implements ItemRepository {

  private final DynamoDbClient client;
  private final String tableName;

  public DynamoDbItemRepository(DynamoDbClient client, String tableName) {
    this.client = client;
    this.tableName = tableName;
  }

  @Override
  public Optional<Item> get(String id) {
    // 強整合読み取りを一回実行する。SDKのエラーはそのまま呼び出し元へ伝える。
    Map<String, AttributeValue> attributes =
        client
            .getItem(
                GetItemRequest.builder()
                    .tableName(tableName)
                    .key(Map.of("id", AttributeValue.fromS(id)))
                    .consistentRead(true)
                    .build())
            .item();
    // 項目がなければ、未登録として空を返す。
    if (attributes == null || attributes.isEmpty()) {
      return Optional.empty();
    }
    AttributeValue itemId = attributes.get("id");
    AttributeValue value = attributes.get("value");
    AttributeValue payload = attributes.get("payload");
    // id・value・payloadが契約どおりの型でそろわなければ、保存データの不正として扱う。
    if (itemId == null || itemId.s() == null || value == null || value.n() == null
        || payload == null || payload.s() == null) {
      throw new IllegalStateException("DynamoDB項目の形式が不正です");
    }
    return Optional.of(new Item(itemId.s(), Long.parseLong(value.n()), payload.s()));
  }
}
