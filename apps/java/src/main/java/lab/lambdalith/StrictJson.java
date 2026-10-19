package lab.lambdalith;

import tools.jackson.core.JacksonException;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 共通API契約が求める厳密さでJSON本文を解析する。
 *
 * <p>重複キーは後勝ちにせず不正とし、整数トークンと小数（1 と 1.0）を区別したまま値を返す。 不正なUTF-8も解析失敗になる。
 */
public final class StrictJson {

  private static final JsonMapper MAPPER =
      JsonMapper.builder()
          .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
          .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
          .build();

  private StrictJson() {}

  /** 構文不正・UTF-8不正・重複キーのいずれかなら invalid_json の検証エラーを投げる。 */
  public static JsonNode parse(byte[] body) {
    try {
      JsonNode value = MAPPER.readTree(body);
      // 空の本文は値を一つも含まないため、構文不正として扱う。
      if (value == null || value.isMissingNode()) {
        throw AppError.invalidJson();
      }
      return value;
    } catch (JacksonException error) {
      throw AppError.invalidJson();
    }
  }
}
