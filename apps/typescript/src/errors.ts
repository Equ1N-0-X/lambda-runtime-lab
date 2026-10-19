// API・検証・ドメイン層で共有する公開エラー応答の分類を定義する。
//
// コード、メッセージ、HTTP状態、詳細文字列はOpenAPI契約で固定されているため、英語のまま維持し、
// 他の言語の実装と同じ本文を返す。

export interface ErrorDetail {
  field: string;
  reason: string;
}

export interface ErrorBody {
  error: {
    code: string;
    message: string;
    details: ErrorDetail[];
  };
}

/** 契約で定めたHTTP状態と本文を持つ、利用者へ返すエラー。 */
export class AppError extends Error {
  private constructor(
    readonly status: 400 | 404 | 422 | 500 | 502,
    readonly code: string,
    readonly publicMessage: string,
    readonly details: ErrorDetail[],
  ) {
    super(`${code}: ${publicMessage}`);
  }

  /** 複数の入力エラーをフィールド名・理由の順に並べ、422応答として保持する。 */
  static validation(details: ErrorDetail[]): AppError {
    const sorted = [...details].sort(
      (left, right) => compare(left.field, right.field) || compare(left.reason, right.reason),
    );
    return new AppError(422, "VALIDATION_ERROR", "request validation failed", sorted);
  }

  /** JSON構文・UTF-8・重複キーの不正を、本文全体の検証エラーとして返す。 */
  static invalidJson(): AppError {
    return AppError.validation([{ field: "$", reason: "invalid_json" }]);
  }

  /** 未対応プロファイルを、利用可能な候補付きの400応答として返す。 */
  static unsupportedProfile(supportedValues: readonly string[]): AppError {
    return new AppError(400, "UNSUPPORTED_PROFILE", "profile is not supported", [
      { field: "profile", reason: `supported_values:${supportedValues.join(",")}` },
    ]);
  }

  /** 検証済みIDに保存項目がない場合の404応答を作る。 */
  static notFound(id: string): AppError {
    return new AppError(404, "NOT_FOUND", "item not found", [
      { field: "id", reason: `not_found:${id}` },
    ]);
  }

  /** ルーターに登録されていない経路へ、詳細なしの404応答を返す。 */
  static routeNotFound(): AppError {
    return new AppError(404, "NOT_FOUND", "item not found", []);
  }

  /** DynamoDBなど外部依存先の障害を502応答へ変換する。 */
  static dependency(): AppError {
    return new AppError(502, "DEPENDENCY_ERROR", "dependency error", []);
  }

  /** 内部計算などの予期しない障害を500応答へ変換する。 */
  static internal(): AppError {
    return new AppError(500, "INTERNAL_ERROR", "internal error", []);
  }

  body(): ErrorBody {
    return { error: { code: this.code, message: this.publicMessage, details: this.details } };
  }
}

/** 契約の並び順（UTF-8バイト順）で比較する。フィールド名と理由はASCIIのため符号単位順と一致する。 */
function compare(left: string, right: string): number {
  if (left < right) return -1;
  return left > right ? 1 : 0;
}
