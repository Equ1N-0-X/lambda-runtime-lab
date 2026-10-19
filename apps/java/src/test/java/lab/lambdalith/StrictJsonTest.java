package lab.lambdalith;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/** 契約が求める厳密なJSON解析（重複キー・末尾の余分な文字・UTF-8）を検証する。 */
class StrictJsonTest {

  static boolean isInvalid(byte[] body) {
    try {
      StrictJson.parse(body);
      return false;
    } catch (AppError error) {
      return error.status() == 422;
    }
  }

  private static boolean isInvalid(String body) {
    return isInvalid(body.getBytes(StandardCharsets.UTF_8));
  }

  @Test
  void rejectsDuplicateMembersAtAnyDepth() {
    assertThat(isInvalid("{\"items\":1,\"items\":2}")).isTrue();
    assertThat(isInvalid("{\"records\":[{\"id\":\"a\",\"id\":\"b\"}]}")).isTrue();
  }

  @Test
  void rejectsMalformedDocuments() {
    assertThat(isInvalid("{\"items\":")).isTrue();
    assertThat(isInvalid("{\"items\":1} x")).isTrue();
    assertThat(isInvalid("")).isTrue();
  }

  @Test
  void keepsTheDistinctionBetweenIntegerAndDecimalTokens() {
    assertThat(StrictJson.parse("{\"v\":1}".getBytes(StandardCharsets.UTF_8)).get("v").isIntegralNumber())
        .isTrue();
    assertThat(StrictJson.parse("{\"v\":1.0}".getBytes(StandardCharsets.UTF_8)).get("v").isIntegralNumber())
        .isFalse();
  }
}
