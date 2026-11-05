package lab.lambdalith;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import org.assertj.core.api.SoftAssertions;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.awscore.exception.AwsErrorDetails;
import software.amazon.awssdk.awscore.exception.AwsServiceException;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** 保存値から公開するItemへの変換と、保存先障害の原因の分類を、全言語共通のケースで検証する。 */
class RepositoryTest {

  private static final Path CASES = Path.of("../../spec/repository-cases/stored-items.json");
  private static final JsonMapper MAPPER = JsonMapper.builder().build();

  @Test
  void storedItemConversionFollowsSharedCases() throws Exception {
    // 全言語で同じ保存値の境界を使い、契約外の保存値を成功の応答にしない。
    SoftAssertions softly = new SoftAssertions();
    for (JsonNode testcase : MAPPER.readTree(CASES.toFile()).get("cases")) {
      String name = testcase.get("name").asString();
      Map<String, AttributeValue> attributes = attributes(testcase.get("attributes"));
      JsonNode expected = testcase.get("expected");
      if (expected.has("item")) {
        JsonNode item = expected.get("item");
        softly
            .assertThat(DynamoDbItemRepository.storedItem(attributes))
            .as(name)
            .isEqualTo(
                new Item(
                    item.get("id").asString(), item.get("value").asLong(), item.get("payload").asString()));
      } else {
        softly
            .assertThatThrownBy(() -> DynamoDbItemRepository.storedItem(attributes))
            .as(name)
            .isInstanceOfSatisfying(
                RepositoryException.class,
                error -> assertThat(error.category()).isEqualTo(expected.get("error").asString()));
      }
    }
    softly.assertAll();
  }

  @Test
  void serviceErrorsAreClassifiedByCode() {
    assertThat(RepositoryException.classify(serviceError("AccessDeniedException")).category())
        .isEqualTo("permission");
    assertThat(RepositoryException.classify(serviceError("ResourceNotFoundException")).category())
        .isEqualTo("table");
    assertThat(RepositoryException.classify(serviceError("ThrottlingException")).category())
        .isEqualTo("throttling");
    RepositoryException unknown = RepositoryException.classify(serviceError("InternalServerError"));
    assertThat(unknown.category()).isEqualTo("unknown");
    assertThat(unknown.errorCode()).isEqualTo("InternalServerError");
    assertThatThrownBy(() -> {
          throw RepositoryException.network();
        })
        .hasMessage("network");
  }

  private static AwsServiceException serviceError(String code) {
    return AwsServiceException.builder()
        .awsErrorDetails(AwsErrorDetails.builder().errorCode(code).errorMessage("secret").build())
        .build();
  }

  private static Map<String, AttributeValue> attributes(JsonNode node) {
    Map<String, AttributeValue> attributes = new HashMap<>();
    for (var member : node.properties()) {
      JsonNode value = member.getValue();
      attributes.put(
          member.getKey(),
          value.has("S")
              ? AttributeValue.fromS(value.get("S").asString())
              : AttributeValue.fromN(value.get("N").asString()));
    }
    return attributes;
  }
}
