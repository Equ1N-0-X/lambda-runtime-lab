// API・検証・ドメイン層で共有する公開エラー応答の分類を定義する。
//
// コード、メッセージ、HTTP状態、詳細文字列はOpenAPI契約で固定されているため、英語のまま維持し、
// 他の言語の実装と同じ本文を返す。

export interface ErrorDetail {
  field: string;
  reason: string;
}

/** 保存先障害を調査するために内部ログへ残す、原因の分類とAWSのエラーコード。 */
export interface DependencyCause {
  category: string;
  errorCode: string | null;
}

export interface ErrorBody {
  error: {
    code: string;
    message: string;
    details: ErrorDetail[];
  };
}

/**
 * 本文の上限（バイト）。有効な要求の最大（約2.45MB）に余裕を持たせ、Lambdaの呼び出しデータの
 * 上限（6MB）より手前で、全言語が同じエラーを返せる大きさにしている。
 */
export const MAX_BODY_BYTES = 4_194_304;

/** 契約で定めたHTTP状態と本文を持つ、利用者へ返すエラー。 */
export class AppError extends Error {
  private constructor(
    readonly status: 400 | 404 | 413 | 422 | 500 | 502,
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

  /** 本文が上限を超える場合の413応答を作る。本文は解析しない。 */
  static payloadTooLarge(): AppError {
    return new AppError(413, "PAYLOAD_TOO_LARGE", "request body too large", [
      { field: "$", reason: `max_bytes:${MAX_BODY_BYTES}` },
    ]);
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

  /** DynamoDBなど外部依存先の障害を502応答へ変換する。原因は内部ログ用にだけ保持する。 */
  static dependency(cause?: DependencyCause): AppError {
    const error = new AppError(502, "DEPENDENCY_ERROR", "dependency error", []);
    error.dependencyCause = cause;
    return error;
  }

  /** 保存先障害の原因の分類。公開する本文には含めない。 */
  dependencyCause?: DependencyCause;

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
