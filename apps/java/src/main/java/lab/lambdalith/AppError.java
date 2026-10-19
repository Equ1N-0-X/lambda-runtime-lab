package lab.lambdalith;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * API・検証・ドメイン層で共有する公開エラー応答。
 *
 * <p>コード、メッセージ、HTTP状態、詳細文字列はOpenAPI契約で固定されているため、英語のまま維持し、
 * 他の言語の実装と同じ本文を返す。
 */
public final class AppError extends RuntimeException {

  private final int status;
  private final String code;
  private final String publicMessage;
  private final List<ErrorDetail> details;

  private AppError(int status, String code, String publicMessage, List<ErrorDetail> details) {
    super(code + ": " + publicMessage);
    this.status = status;
    this.code = code;
    this.publicMessage = publicMessage;
    this.details = List.copyOf(details);
  }

  /** 複数の入力エラーをフィールド名・理由の順（ASCIIのためUTF-8バイト順と同じ）に並べ、422応答にする。 */
  public static AppError validation(List<ErrorDetail> details) {
    List<ErrorDetail> sorted = new ArrayList<>(details);
    sorted.sort(Comparator.comparing(ErrorDetail::field).thenComparing(ErrorDetail::reason));
    return new AppError(422, "VALIDATION_ERROR", "request validation failed", sorted);
  }

  /** JSON構文・UTF-8・重複キーの不正を、本文全体の検証エラーとして返す。 */
  public static AppError invalidJson() {
    return validation(List.of(new ErrorDetail("$", "invalid_json")));
  }

  /** 未対応プロファイルを、利用可能な候補付きの400応答として返す。 */
  public static AppError unsupportedProfile(List<String> supportedValues) {
    return new AppError(
        400,
        "UNSUPPORTED_PROFILE",
        "profile is not supported",
        List.of(new ErrorDetail("profile", "supported_values:" + String.join(",", supportedValues))));
  }

  /** 検証済みIDに保存項目がない場合の404応答を作る。 */
  public static AppError notFound(String id) {
    return new AppError(
        404, "NOT_FOUND", "item not found", List.of(new ErrorDetail("id", "not_found:" + id)));
  }

  /** ルーターに登録されていない経路へ、詳細なしの404応答を返す。 */
  public static AppError routeNotFound() {
    return new AppError(404, "NOT_FOUND", "item not found", List.of());
  }

  /** DynamoDBなど外部依存先の障害を502応答へ変換する。 */
  public static AppError dependency() {
    return new AppError(502, "DEPENDENCY_ERROR", "dependency error", List.of());
  }

  /** 内部計算などの予期しない障害を500応答へ変換する。 */
  public static AppError internal() {
    return new AppError(500, "INTERNAL_ERROR", "internal error", List.of());
  }

  public int status() {
    return status;
  }

  public List<ErrorDetail> details() {
    return details;
  }

  public Map<String, Object> body() {
    return Map.of("error", Map.of("code", code, "message", publicMessage, "details", details));
  }
}
