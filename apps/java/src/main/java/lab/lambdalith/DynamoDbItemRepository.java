package lab.lambdalith;

import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import software.amazon.awssdk.awscore.exception.AwsServiceException;
import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;

/** DynamoDBから強整合読み取りで一件ずつ取得する保存先。 */
public final class DynamoDbItemRepository implements ItemRepository {

  private static final Pattern ITEM_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,127}");
  // 保存値の数値は、DynamoDBが正規化して返す10進整数の表記だけを受け付ける（全言語で共通）。
  private static final Pattern INTEGER = Pattern.compile("-?[0-9]+");
  private static final int MAX_PAYLOAD_CODE_POINTS = 4096;
  private static final long MIN_ITEM_VALUE = -1_000_000_000L;
  private static final long MAX_ITEM_VALUE = 1_000_000_000L;

  private final DynamoDbClient client;
  private final String tableName;

  public DynamoDbItemRepository(DynamoDbClient client, String tableName) {
    this.client = client;
    this.tableName = tableName;
  }

  @Override
  public Optional<Item> get(String id) {
    // 強整合読み取りを一回実行する。SDKのエラーは原因を分類して呼び出し元へ伝える。
    Map<String, AttributeValue> attributes;
    try {
      attributes =
          client
              .getItem(
                  GetItemRequest.builder()
                      .tableName(tableName)
                      .key(Map.of("id", AttributeValue.fromS(id)))
                      .consistentRead(true)
                      .build())
              .item();
    } catch (AwsServiceException error) {
      throw RepositoryException.classify(error);
    } catch (SdkClientException error) {
      // AWSが応答していない通信・タイムアウト等は、通信の問題として扱う。
      throw RepositoryException.network();
    }
    // 項目がなければ、未登録として空を返す。
    if (attributes == null || attributes.isEmpty()) {
      return Optional.empty();
    }
    return Optional.of(storedItem(attributes));
  }

  /**
   * DynamoDBの属性を公開するItemへ変換する。
   *
   * <p>公開するItemの境界（IDの形式・値の範囲・payloadの長さ）を満たさない保存値は、
   * 成功の応答にせず保存データの不正とする。
   */
  static Item storedItem(Map<String, AttributeValue> attributes) {
    AttributeValue itemId = attributes.get("id");
    AttributeValue value = attributes.get("value");
    AttributeValue payload = attributes.get("payload");
    // id・value・payloadが契約どおりの型と形式でそろわなければ、保存データの不正として扱う。
    if (itemId == null || itemId.s() == null || value == null || value.n() == null
        || payload == null || payload.s() == null
        || !ITEM_ID.matcher(itemId.s()).matches()
        || !INTEGER.matcher(value.n()).matches()
        || payload.s().codePointCount(0, payload.s().length()) > MAX_PAYLOAD_CODE_POINTS) {
      throw RepositoryException.invalidItem();
    }
    long parsed;
    try {
      parsed = Long.parseLong(value.n());
    } catch (NumberFormatException overflow) {
      // 64bitに収まらない値は範囲外のため、契約外の保存値として扱う。
      throw RepositoryException.invalidItem();
    }
    if (parsed < MIN_ITEM_VALUE || parsed > MAX_ITEM_VALUE) {
      throw RepositoryException.invalidItem();
    }
    return new Item(itemId.s(), parsed, payload.s());
  }
}
