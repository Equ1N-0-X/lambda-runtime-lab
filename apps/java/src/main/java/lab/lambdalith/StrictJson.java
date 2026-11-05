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

  /** キーと文字列の値に、対のないサロゲートが含まれていないかを再帰的に確かめる。 */
  private static boolean containsOnlyScalarValues(JsonNode node) {
    if (node.isString()) {
      return isWellFormed(node.asString());
    }
    // オブジェクトはキーと値の両方、配列は各要素を確かめる。数値・真偽値・nullは対象外。
    if (node.isObject()) {
      for (var member : node.properties()) {
        if (!isWellFormed(member.getKey()) || !containsOnlyScalarValues(member.getValue())) {
          return false;
        }
      }
    } else if (node.isArray()) {
      for (JsonNode element : node) {
        if (!containsOnlyScalarValues(element)) {
          return false;
        }
      }
    }
    return true;
  }

  /** 上位サロゲートの直後に下位サロゲートがある組だけを許し、単独のサロゲートを拒否する。 */
  private static boolean isWellFormed(String text) {
    for (int index = 0; index < text.length(); index++) {
      char character = text.charAt(index);
      if (Character.isHighSurrogate(character)
          && index + 1 < text.length()
          && Character.isLowSurrogate(text.charAt(index + 1))) {
        index++;
      } else if (Character.isSurrogate(character)) {
        return false;
      }
    }
    return true;
  }

  /** 構文不正・UTF-8不正・重複キー・対のないサロゲートのいずれかなら invalid_json の検証エラーを投げる。 */
  public static JsonNode parse(byte[] body) {
    try {
      JsonNode value = MAPPER.readTree(body);
      // 空の本文は値を一つも含まないため、構文不正として扱う。
      if (value == null || value.isMissingNode()) {
        throw AppError.invalidJson();
      }
      // \uD800 のような対のないサロゲートはUnicodeのスカラー値ではないため、正本ではJSONの解析失敗。
      if (!containsOnlyScalarValues(value)) {
        throw AppError.invalidJson();
      }
      return value;
    } catch (JacksonException error) {
      throw AppError.invalidJson();
    }
  }
}
