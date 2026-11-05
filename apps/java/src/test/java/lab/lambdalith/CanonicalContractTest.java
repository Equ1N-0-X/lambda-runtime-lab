package lab.lambdalith;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.assertj.core.api.SoftAssertions;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 共有する正規テストベクトル（spec/testcases）をJava実装へ適用する。
 *
 * <p>他の言語の実装も同じファイルを使うため、デプロイ環境なしに応答の一致を確かめられる。
 */
class CanonicalContractTest {

  private static final Path TESTCASES = Path.of("../../spec/testcases");
  private static final JsonMapper MAPPER = JsonMapper.builder().build();

  @Test
  void everyCanonicalCaseMatches() throws Exception {
    List<JsonNode> documents = documents();
    MockMvc mvc = TestApp.mockMvc(new InMemoryItemRepository(fixtureItems(documents)));

    int executed = 0;
    // 一つの不一致で止めず、全ケースの不一致をまとめて報告する。
    SoftAssertions softly = new SoftAssertions();
    for (JsonNode document : documents) {
      for (JsonNode testcase : document.get("cases")) {
        String name = testcase.get("name").asString();
        JsonNode request = testcase.get("request");
        MockHttpServletResponse response =
            TestApp.sendBytes(
                mvc,
                request.get("method").asString(),
                request.get("path").asString(),
                requestBody(request),
                Map.of());

        JsonNode expected = testcase.get("expected");
        softly.assertThat(response.getStatus()).as(name + ": status").isEqualTo(expected.get("status").asInt());
        softly
            .assertThat(MAPPER.readTree(response.getContentAsByteArray()))
            .as(name + ": body")
            .isEqualTo(expected.get("body"));
        executed++;
      }
    }
    softly.assertAll();
    assertThat(executed).isGreaterThanOrEqualTo(12);
  }

  /**
   * ケースの本文を送信するバイト列へ変換する。生の本文は組み立て直さずにそのまま送る。
   *
   * <p>raw_body・raw_body_base64は、JSONとして不正な本文や数値の表記をそのまま送るために使う。
   */
  private static byte[] requestBody(JsonNode request) throws IOException {
    if (request.has("raw_body_base64")) {
      return Base64.getDecoder().decode(request.get("raw_body_base64").asString());
    }
    if (request.has("raw_body")) {
      return request.get("raw_body").asString().getBytes(StandardCharsets.UTF_8);
    }
    JsonNode body = request.get("body");
    return body == null ? null : MAPPER.writeValueAsBytes(body);
  }

  private static List<JsonNode> documents() throws IOException {
    try (Stream<Path> files = Files.list(TESTCASES)) {
      List<JsonNode> documents = new ArrayList<>();
      for (Path file : files.filter(path -> path.toString().endsWith(".json")).sorted().toList()) {
        JsonNode document = MAPPER.readTree(file.toFile());
        JsonNode implementations = document.path("implementations");
        // 一部の言語だけで実装済みのケースファイルは、Javaが対象に含まれる場合だけ使う。
        boolean applies =
            implementations.isMissingNode()
                || implementations.valueStream().anyMatch(key -> "java".equals(key.asString()));
        if (applies) {
          documents.add(document);
        }
      }
      return documents;
    }
  }

  private static List<Item> fixtureItems(List<JsonNode> documents) {
    Map<String, Item> items = new LinkedHashMap<>();
    for (JsonNode document : documents) {
      JsonNode fixtures = document.path("fixtures").path("items");
      for (JsonNode fixture : fixtures) {
        String id = fixture.get("id").asString();
        items.putIfAbsent(
            id, new Item(id, fixture.get("value").asLong(), fixture.get("payload").asString()));
      }
    }
    return List.copyOf(items.values());
  }

  static MediaType json() {
    return MediaType.APPLICATION_JSON;
  }
}
