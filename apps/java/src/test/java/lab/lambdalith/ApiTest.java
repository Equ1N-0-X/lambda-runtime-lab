package lab.lambdalith;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** 正規テストケースで網羅しない入力境界、ログ相関、依存先障害の扱いを検証する。 */
class ApiTest {

  private static final JsonMapper MAPPER = JsonMapper.builder().build();
  private final MockMvc mvc =
      TestApp.mockMvc(new InMemoryItemRepository(List.of(new Item("item-001", 123, "payload-001"))));

  private JsonNode details(MockHttpServletResponse response) throws Exception {
    return MAPPER.readTree(response.getContentAsByteArray()).path("error").path("details");
  }

  @Test
  void rejectsFloatsAndStringsForIntegersWithoutCoercion() throws Exception {
    MockHttpServletResponse response =
        TestApp.send(
            mvc,
            "POST",
            "/compute",
            "{\"items\":1.0,\"iterations\":\"1\",\"seed\":\"s\",\"profile\":\"sequential\"}",
            Map.of());

    assertThat(response.getStatus()).isEqualTo(422);
    assertThat(details(response).toString())
        .isEqualTo(
            "[{\"field\":\"items\",\"reason\":\"type:integer\"},"
                + "{\"field\":\"iterations\",\"reason\":\"type:integer\"}]");
  }

  @Test
  void reportsHugeIntegersAsRangeViolations() throws Exception {
    MockHttpServletResponse response =
        TestApp.send(
            mvc,
            "POST",
            "/compute",
            "{\"items\":99999999999999999999,\"iterations\":-99999999999999999999,"
                + "\"seed\":\"s\",\"profile\":\"sequential\"}",
            Map.of());

    assertThat(details(response).toString())
        .isEqualTo(
            "[{\"field\":\"items\",\"reason\":\"maximum:1000\"},"
                + "{\"field\":\"iterations\",\"reason\":\"minimum:1\"}]");
  }

  @Test
  void duplicateMembersAndInvalidUtf8AreInvalidJson() throws Exception {
    MockHttpServletResponse duplicate =
        TestApp.send(mvc, "POST", "/transform", "{\"records\":[],\"records\":[]}", Map.of());

    assertThat(duplicate.getStatus()).isEqualTo(422);
    assertThat(details(duplicate).toString())
        .isEqualTo("[{\"field\":\"$\",\"reason\":\"invalid_json\"}]");
    assertThat(StrictJsonTest.isInvalid(new byte[] {(byte) 0xff, (byte) 0xfe})).isTrue();
  }

  @Test
  void validatesBenchmarkRunHeaderBeforeBody() throws Exception {
    MockHttpServletResponse response =
        TestApp.send(
            mvc, "POST", "/transform", "{", Map.of("X-Benchmark-Run-Id", "-not a run id"));

    assertThat(details(response).toString())
        .isEqualTo("[{\"field\":\"headers.X-Benchmark-Run-Id\",\"reason\":\"pattern\"}]");
  }

  @Test
  void storageErrorsBecome502AndAreLoggedAsWarn() throws Exception {
    ByteArrayOutputStream logs = new ByteArrayOutputStream();
    ItemRepository failing =
        id -> {
          throw new IllegalStateException("timeout");
        };
    MockMvc failingMvc = TestApp.mockMvc(failing, new PrintStream(logs, true, StandardCharsets.UTF_8));

    MockHttpServletResponse response = TestApp.send(failingMvc, "GET", "/items/item-001", null, Map.of());

    assertThat(response.getStatus()).isEqualTo(502);
    JsonNode line = MAPPER.readTree(logs.toString(StandardCharsets.UTF_8).strip());
    assertThat(line.get("level").asString()).isEqualTo("WARN");
    assertThat(line.get("route").asString()).isEqualTo("/items/{id}");
  }

  @Test
  void writesOneJsonLineWithCorrelationFieldsAtTopLevel() throws Exception {
    ByteArrayOutputStream logs = new ByteArrayOutputStream();
    MockMvc logged =
        TestApp.mockMvc(
            new InMemoryItemRepository(List.of(new Item("item-001", 123, "payload-001"))),
            new PrintStream(logs, true, StandardCharsets.UTF_8));

    TestApp.send(
        logged,
        "POST",
        "/aggregate",
        "{\"ids\":[\"item-001\"],\"profile\":\"concurrent\"}",
        Map.of(
            "X-Benchmark-Run-Id", "arm64-512-health-20260101T000000Z",
            "X-Benchmark-Request-Index", "-3"));

    List<String> lines = logs.toString(StandardCharsets.UTF_8).lines().toList();
    assertThat(lines).hasSize(1);
    JsonNode line = MAPPER.readTree(lines.get(0));
    assertThat(line.get("implementation").asString()).isEqualTo("java");
    assertThat(line.get("benchmark_run_id").asString()).isEqualTo("arm64-512-health-20260101T000000Z");
    assertThat(line.get("benchmark_request_index").asLong()).isEqualTo(-3);
    assertThat(line.get("profile").asString()).isEqualTo("concurrent");
    assertThat(line.get("status_code").asInt()).isEqualTo(200);
  }

  @Test
  void unknownRoutesReturnCanonicalNotFoundBody() throws Exception {
    MockHttpServletResponse response = TestApp.send(mvc, "GET", "/unknown", null, Map.of());

    assertThat(response.getStatus()).isEqualTo(404);
    assertThat(response.getContentAsString())
        .contains("\"code\":\"NOT_FOUND\"")
        .contains("\"details\":[]");
  }

  @Test
  void requestIndexDistinguishesMeasurementWarmupAndInvalidValues() {
    assertThat(RequestLoggingFilter.requestIndex("7")).isEqualTo(7);
    assertThat(RequestLoggingFilter.requestIndex("-3")).isEqualTo(-3);
    assertThat(RequestLoggingFilter.requestIndex("invalid")).isZero();
    assertThat(RequestLoggingFilter.requestIndex(null)).isZero();
  }
}
