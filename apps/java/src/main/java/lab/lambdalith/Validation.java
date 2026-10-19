package lab.lambdalith;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.IntFunction;
import java.util.function.Predicate;
import java.util.regex.Pattern;
import lab.lambdalith.Requests.AggregateRequest;
import lab.lambdalith.Requests.ComputeRequest;
import lab.lambdalith.Requests.TransformRecord;
import lab.lambdalith.Requests.TransformRequest;
import tools.jackson.databind.JsonNode;

/**
 * 共通API契約に従い、解析済みJSONを検証して型付きの要求へ変換する。
 *
 * <p>不正入力で返すフィールド位置と理由は契約で固定されているため、Bean Validationの自動エラーへ委譲せず、
 * 他の言語の実装と同じ順序で検査する。
 */
public final class Validation {

  public static final int MAX_RECORDS = 1_000;
  public static final int MAX_TAGS = 32;
  public static final int MAX_IDS = 100;
  public static final long MAX_COMPUTE_ITEMS = 1_000;
  public static final long MAX_COMPUTE_ITERATIONS = 1_000_000;
  public static final long MAX_COMPUTE_WORK = 1_000_000;
  public static final long MIN_ITEM_VALUE = -1_000_000_000;
  public static final long MAX_ITEM_VALUE = 1_000_000_000;

  public static final List<String> COMPUTE_PROFILES = List.of("sequential");
  public static final List<String> AGGREGATE_PROFILES = List.of("sequential", "concurrent");

  public static final String BENCHMARK_RUN_ID_FIELD = "headers.X-Benchmark-Run-Id";

  private static final Pattern ITEM_ID_PATTERN = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]*$");
  private static final Pattern PRINTABLE_ASCII_PATTERN = Pattern.compile("^[ -~]*$");

  private record StringSpec(int minLength, int maxLength, Predicate<String> pattern) {}

  private static final StringSpec ITEM_ID =
      new StringSpec(1, 128, text -> ITEM_ID_PATTERN.matcher(text).matches());
  private static final StringSpec RECORD_NAME =
      new StringSpec(0, 256, text -> PRINTABLE_ASCII_PATTERN.matcher(text).matches());
  private static final StringSpec TAG =
      new StringSpec(0, 64, text -> PRINTABLE_ASCII_PATTERN.matcher(text).matches());
  private static final StringSpec SEED = new StringSpec(1, 256, text -> true);
  private static final StringSpec PROFILE = new StringSpec(0, 256, text -> true);

  private Validation() {}

  public static TransformRequest transformRequest(JsonNode document) {
    List<ErrorDetail> details = new ArrayList<>();
    // ルートがオブジェクトでない場合は各項目を読めないため、直ちにルートの検証エラーを返す。
    if (!object(document, "$", details)) {
      throw AppError.validation(details);
    }
    rejectUnknownMembers(document, Set.of("records"), "", details);

    List<TransformRecord> records = new ArrayList<>();
    int recordCount = 0;
    JsonNode items = arrayMember(document, "", "records", 1, MAX_RECORDS, details);
    // recordsが配列かつ件数範囲内の場合だけ、各要素をレコードとして検証・変換する。
    if (items != null) {
      recordCount = items.size();
      for (int index = 0; index < items.size(); index++) {
        TransformRecord record = transformRecord(items.get(index), index, details);
        if (record != null) {
          records.add(record);
        }
      }
    }
    // 項目エラーが一件でもある、または変換成功数が入力件数と異なる場合は成功要求を作らない。
    if (!details.isEmpty() || records.size() != recordCount) {
      throw AppError.validation(details);
    }
    return new TransformRequest(records);
  }

  public static ComputeRequest computeRequest(JsonNode document) {
    List<ErrorDetail> details = new ArrayList<>();
    // ルートがオブジェクトでない場合は各計算項目を読めないため、ルートの検証エラーを返す。
    if (!object(document, "$", details)) {
      throw AppError.validation(details);
    }
    rejectUnknownMembers(document, Set.of("items", "iterations", "seed", "profile"), "", details);

    Long items = integerMember(document, "", "items", 1, MAX_COMPUTE_ITEMS, details);
    Long iterations = integerMember(document, "", "iterations", 1, MAX_COMPUTE_ITERATIONS, details);
    String seed = stringMember(document, "", "seed", SEED, details);
    String profile = stringMember(document, "", "profile", PROFILE, details);

    // 四つの必須項目がすべて取得でき、追加の検証エラーもない場合だけ計算要求を確定する。
    if (items == null || iterations == null || seed == null || profile == null
        || !details.isEmpty()) {
      throw AppError.validation(details);
    }
    return new ComputeRequest(items, iterations, seed, profile);
  }

  public static AggregateRequest aggregateRequest(JsonNode document) {
    List<ErrorDetail> details = new ArrayList<>();
    // ルートがオブジェクトでない場合はID一覧とprofileを読めないため、ルートの検証エラーを返す。
    if (!object(document, "$", details)) {
      throw AppError.validation(details);
    }
    rejectUnknownMembers(document, Set.of("ids", "profile"), "", details);

    List<String> ids = new ArrayList<>();
    int idCount = 0;
    JsonNode items = arrayMember(document, "", "ids", 1, MAX_IDS, details);
    // idsが配列かつ件数範囲内の場合だけ、各要素をIDとして検証・変換する。
    if (items != null) {
      idCount = items.size();
      for (int index = 0; index < items.size(); index++) {
        String id = stringValue(items.get(index), "ids[" + index + "]", ITEM_ID, details);
        if (id != null) {
          ids.add(id);
        }
      }
    }
    String profile = stringMember(document, "", "profile", PROFILE, details);

    // profileが取得でき、全IDの変換にも失敗していない場合だけ集約要求を確定する。
    if (profile == null || !details.isEmpty() || ids.size() != idCount) {
      throw AppError.validation(details);
    }
    return new AggregateRequest(ids, profile);
  }

  public static void checkItemId(String id) {
    checkedString(id, "id", ITEM_ID);
  }

  /** 計測相関ヘッダーはBenchmark Runnerの実行IDと同じ文字種を受け付ける。 */
  public static void checkBenchmarkRunId(String runId) {
    checkedString(runId, BENCHMARK_RUN_ID_FIELD, ITEM_ID);
  }

  public static void checkTransformSemantics(TransformRequest request) {
    List<ErrorDetail> details =
        duplicateDetails(
            request.records().stream().map(TransformRecord::id).toList(),
            index -> "records[" + index + "].id");
    // 重複IDがなければ成功とし、一件でもあれば重複位置をまとめた422エラーを返す。
    if (!details.isEmpty()) {
      throw AppError.validation(details);
    }
  }

  public static void checkComputeSemantics(ComputeRequest request) {
    // 項目数と反復回数の積が計測用上限を超える場合は、過度なCPU消費を防ぐため拒否する。
    if (request.items() * request.iterations() > MAX_COMPUTE_WORK) {
      throw AppError.validation(List.of(new ErrorDetail("$", "maximum_work:" + MAX_COMPUTE_WORK)));
    }
    // プロファイル名が計算APIで許可した方式に含まれない場合は、400エラーを返す。
    if (!COMPUTE_PROFILES.contains(request.profile())) {
      throw AppError.unsupportedProfile(COMPUTE_PROFILES);
    }
  }

  public static void checkAggregateSemantics(AggregateRequest request) {
    List<ErrorDetail> details = duplicateDetails(request.ids(), index -> "ids[" + index + "]");
    // 同じIDを複数回集約すると二重計上になるため、重複が一件でもあれば拒否する。
    if (!details.isEmpty()) {
      throw AppError.validation(details);
    }
    // プロファイル名が直列・並行の許可方式に含まれない場合は、400エラーを返す。
    if (!AGGREGATE_PROFILES.contains(request.profile())) {
      throw AppError.unsupportedProfile(AGGREGATE_PROFILES);
    }
  }

  private static void checkedString(String value, String field, StringSpec spec) {
    List<ErrorDetail> details = new ArrayList<>();
    checkString(value, field, spec, details);
    // 文字列検証で詳細が一件でもあれば422エラーへ変換する。
    if (!details.isEmpty()) {
      throw AppError.validation(details);
    }
  }

  /** 二回目以降に現れた値の位置を、重複として列挙する。 */
  private static List<ErrorDetail> duplicateDetails(List<String> values, IntFunction<String> field) {
    Set<String> seen = new HashSet<>();
    List<ErrorDetail> details = new ArrayList<>();
    for (int index = 0; index < values.size(); index++) {
      if (!seen.add(values.get(index))) {
        details.add(new ErrorDetail(field.apply(index), "duplicate"));
      }
    }
    return details;
  }

  private static TransformRecord transformRecord(
      JsonNode document, int index, List<ErrorDetail> details) {
    String prefix = "records[" + index + "]";
    if (!object(document, prefix, details)) {
      return null;
    }
    rejectUnknownMembers(document, Set.of("id", "name", "tags", "value"), prefix, details);

    String id = stringMember(document, prefix, "id", ITEM_ID, details);
    String name = stringMember(document, prefix, "name", RECORD_NAME, details);
    List<String> tags = tagMember(document, prefix, details);
    Long value = integerMember(document, prefix, "value", MIN_ITEM_VALUE, MAX_ITEM_VALUE, details);
    // 全項目が検証に成功した場合だけ、変換済みレコードとして返す。
    if (id == null || name == null || tags == null || value == null) {
      return null;
    }
    return new TransformRecord(id, name, tags, value);
  }

  private static List<String> tagMember(JsonNode record, String prefix, List<ErrorDetail> details) {
    JsonNode items = arrayMember(record, prefix, "tags", 0, MAX_TAGS, details);
    if (items == null) {
      return null;
    }
    List<String> tags = new ArrayList<>();
    for (int index = 0; index < items.size(); index++) {
      String tag = stringValue(items.get(index), prefix + ".tags[" + index + "]", TAG, details);
      if (tag != null) {
        tags.add(tag);
      }
    }
    // 一つでも不正なタグがあれば、レコード全体を不成立とする。
    return tags.size() == items.size() ? tags : null;
  }

  private static boolean object(JsonNode document, String field, List<ErrorDetail> details) {
    // JSON値がオブジェクトでなければ、対象フィールドの型不正を記録する。
    if (document.isObject()) {
      return true;
    }
    details.add(new ErrorDetail(field, "type:object"));
    return false;
  }

  private static void rejectUnknownMembers(
      JsonNode members, Set<String> allowed, String prefix, List<ErrorDetail> details) {
    for (Map.Entry<String, JsonNode> entry : members.properties()) {
      // キーが許可リストにない場合は、未定義の入力項目として検証エラーへ追加する。
      if (!allowed.contains(entry.getKey())) {
        details.add(
            new ErrorDetail(fieldPath(prefix, entry.getKey()), "additional_property_not_allowed"));
      }
    }
  }

  private static String fieldPath(String prefix, String name) {
    // ルート項目なら名前だけ、入れ子項目なら親パスとドットで連結してエラー位置を表す。
    return prefix.isEmpty() ? name : prefix + "." + name;
  }

  private static JsonNode member(
      JsonNode members, String prefix, String name, List<ErrorDetail> details) {
    // 指定名のメンバーがあれば値（JSONのnullを含む）を返し、なければ必須項目不足として記録する。
    if (members.has(name)) {
      return members.get(name);
    }
    details.add(new ErrorDetail(fieldPath(prefix, name), "required"));
    return null;
  }

  private static String stringMember(
      JsonNode members, String prefix, String name, StringSpec spec, List<ErrorDetail> details) {
    JsonNode value = member(members, prefix, name, details);
    return value == null ? null : stringValue(value, fieldPath(prefix, name), spec, details);
  }

  private static String stringValue(
      JsonNode document, String field, StringSpec spec, List<ErrorDetail> details) {
    if (!document.isString()) {
      details.add(new ErrorDetail(field, "type:string"));
      return null;
    }
    return checkString(document.stringValue(), field, spec, details);
  }

  private static String checkString(
      String text, String field, StringSpec spec, List<ErrorDetail> details) {
    // 文字数は契約どおりUnicodeのコードポイント数で数える（length() はUTF-16単位のため使わない）。
    int length = text.codePointCount(0, text.length());
    // 文字数が最小値未満、最大値超過、またはパターン不一致なら、その最初の理由を記録して失敗する。
    if (length < spec.minLength()) {
      details.add(new ErrorDetail(field, "min_length:" + spec.minLength()));
      return null;
    }
    if (length > spec.maxLength()) {
      details.add(new ErrorDetail(field, "max_length:" + spec.maxLength()));
      return null;
    }
    if (!spec.pattern().test(text)) {
      details.add(new ErrorDetail(field, "pattern"));
      return null;
    }
    return text;
  }

  private static Long integerMember(
      JsonNode members,
      String prefix,
      String name,
      long minimum,
      long maximum,
      List<ErrorDetail> details) {
    String field = fieldPath(prefix, name);
    JsonNode value = member(members, prefix, name, details);
    if (value == null) {
      return null;
    }
    // 数値以外、または小数点・指数表記の数値（1.0 など）は整数型の不正とする。
    if (!value.isIntegralNumber()) {
      details.add(new ErrorDetail(field, "type:integer"));
      return null;
    }
    // 大きな整数も精度を失わずに比較するため、BigIntegerで上下限と比べる。
    BigInteger number = value.bigIntegerValue();
    if (number.compareTo(BigInteger.valueOf(minimum)) < 0) {
      details.add(new ErrorDetail(field, "minimum:" + minimum));
      return null;
    }
    if (number.compareTo(BigInteger.valueOf(maximum)) > 0) {
      details.add(new ErrorDetail(field, "maximum:" + maximum));
      return null;
    }
    return number.longValue();
  }

  private static JsonNode arrayMember(
      JsonNode members,
      String prefix,
      String name,
      int minItems,
      int maxItems,
      List<ErrorDetail> details) {
    String field = fieldPath(prefix, name);
    JsonNode value = member(members, prefix, name, details);
    if (value == null) {
      return null;
    }
    if (!value.isArray()) {
      details.add(new ErrorDetail(field, "type:array"));
      return null;
    }
    // 配列件数が最小値未満または最大値超過なら、要素を処理せず配列全体の検証エラーにする。
    if (value.size() < minItems) {
      details.add(new ErrorDetail(field, "min_items:" + minItems));
      return null;
    }
    if (value.size() > maxItems) {
      details.add(new ErrorDetail(field, "max_items:" + maxItems));
      return null;
    }
    return value;
  }
}
