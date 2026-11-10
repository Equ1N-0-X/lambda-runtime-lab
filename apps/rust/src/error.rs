//! API・検証・ドメイン層で共有する公開エラー応答の分類を定義する。
//!
//! コード、メッセージ、HTTP状態、詳細文字列はOpenAPI契約で固定されているため、ログや
//! コメントとは異なり英語のまま維持し、Python実装と同じ本文を返す。

use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde::Serialize;

use crate::repository::RepositoryError;

/// 本文の上限（バイト）。有効な要求の最大（約2.45MB）に余裕を持たせ、Lambdaの呼び出しデータの
/// 上限（6MB）より手前で、全言語が同じエラーを返せる大きさにしている。
pub const MAX_BODY_BYTES: usize = 4_194_304;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ErrorDetail {
    pub field: String,
    pub reason: String,
}

impl ErrorDetail {
    /// 検証失敗のフィールド位置と、契約で定めた理由を一組にする。
    pub fn new(field: impl Into<String>, reason: impl Into<String>) -> Self {
        Self {
            field: field.into(),
            reason: reason.into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ErrorBody {
    pub error: ErrorContent,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ErrorContent {
    pub code: &'static str,
    pub message: &'static str,
    pub details: Vec<ErrorDetail>,
}

#[derive(Clone, Debug)]
pub struct AppError {
    status: StatusCode,
    content: ErrorContent,
    // 保存先障害の原因の分類。公開する本文には含めず、応答の拡張としてログへ渡す。
    dependency_cause: Option<DependencyCause>,
}

impl AppError {
    /// 複数の入力エラーを安定した順序に並べ、422応答として保持する。
    pub fn validation(mut details: Vec<ErrorDetail>) -> Self {
        details.sort_by(|left, right| {
            left.field
                .cmp(&right.field)
                .then_with(|| left.reason.cmp(&right.reason))
        });
        Self {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            content: ErrorContent {
                code: "VALIDATION_ERROR",
                message: "request validation failed",
                details,
            },
            dependency_cause: None,
        }
    }

    /// JSON構文・UTF-8・重複キーの不正を、本文全体の検証エラーとして返す。
    pub fn invalid_json() -> Self {
        Self::validation(vec![ErrorDetail::new("$", "invalid_json")])
    }

    /// 本文が上限を超える場合の413応答を作る。本文は解析しない。
    pub fn payload_too_large() -> Self {
        Self {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            content: ErrorContent {
                code: "PAYLOAD_TOO_LARGE",
                message: "request body too large",
                details: vec![ErrorDetail::new("$", format!("max_bytes:{MAX_BODY_BYTES}"))],
            },
            dependency_cause: None,
        }
    }

    /// 未対応プロファイルを、利用可能な候補付きの400応答として返す。
    pub fn unsupported_profile(supported_values: &[&str]) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            content: ErrorContent {
                code: "UNSUPPORTED_PROFILE",
                message: "profile is not supported",
                details: vec![ErrorDetail::new(
                    "profile",
                    format!("supported_values:{}", supported_values.join(",")),
                )],
            },
            dependency_cause: None,
        }
    }

    /// 検証済みIDに保存項目がない場合の404応答を作る。
    pub fn not_found(id: &str) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            content: ErrorContent {
                code: "NOT_FOUND",
                message: "item not found",
                details: vec![ErrorDetail::new("id", format!("not_found:{id}"))],
            },
            dependency_cause: None,
        }
    }

    /// ルーターに登録されていない経路へ、詳細なしの404応答を返す。
    pub fn route_not_found() -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            content: ErrorContent {
                code: "NOT_FOUND",
                message: "item not found",
                details: Vec::new(),
            },
            dependency_cause: None,
        }
    }

    /// DynamoDBなど外部依存先の障害を、原因の分類を保持したまま502応答へ変換する。
    pub fn dependency(error: RepositoryError) -> Self {
        Self {
            status: StatusCode::BAD_GATEWAY,
            content: ErrorContent {
                code: "DEPENDENCY_ERROR",
                message: "dependency error",
                details: Vec::new(),
            },
            dependency_cause: Some(DependencyCause {
                category: error.category.as_str(),
                error_code: error.error_code,
            }),
        }
    }

    /// 内部計算や非同期タスクの予期しない障害を500応答へ変換する。
    pub fn internal() -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            content: ErrorContent {
                code: "INTERNAL_ERROR",
                message: "internal error",
                details: Vec::new(),
            },
            dependency_cause: None,
        }
    }

    pub fn status(&self) -> StatusCode {
        self.status
    }

    pub fn details(&self) -> &[ErrorDetail] {
        &self.content.details
    }
}

/// 保存先障害を調査するために内部ログへ残す、原因の分類とAWSのエラーコード。
#[derive(Clone, Debug)]
pub struct DependencyCause {
    pub category: &'static str,
    pub error_code: Option<String>,
}

impl IntoResponse for AppError {
    /// 内部エラー型を、契約どおりのHTTP状態とJSON本文へ変換する。
    fn into_response(self) -> Response {
        let mut response = (
            self.status,
            Json(ErrorBody {
                error: self.content,
            }),
        )
            .into_response();
        // 原因の分類は本文に出さず、ログを出す処理だけが読める応答の拡張に入れる。
        if let Some(cause) = self.dependency_cause {
            response.extensions_mut().insert(cause);
        }
        response
    }
}
