package lab.lambdalith;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
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
    for (JsonNode document : documents) {
      for (JsonNode testcase : document.get("cases")) {
        String name = testcase.get("name").asString();
        JsonNode request = testcase.get("request");
        JsonNode body = request.get("body");
        MockHttpServletResponse response =
            TestApp.send(
                mvc,
                request.get("method").asString(),
                request.get("path").asString(),
                body == null ? null : MAPPER.writeValueAsString(body),
                Map.of());

        JsonNode expected = testcase.get("expected");
        assertThat(response.getStatus()).as(name + ": status").isEqualTo(expected.get("status").asInt());
        assertThat(MAPPER.readTree(response.getContentAsByteArray()))
            .as(name + ": body")
            .isEqualTo(expected.get("body"));
        executed++;
      }
    }
    assertThat(executed).isGreaterThanOrEqualTo(12);
  }

  private static List<JsonNode> documents() throws IOException {
    try (Stream<Path> files = Files.list(TESTCASES)) {
      List<JsonNode> documents = new ArrayList<>();
      for (Path file : files.filter(path -> path.toString().endsWith(".json")).sorted().toList()) {
        documents.add(MAPPER.readTree(file.toFile()));
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
