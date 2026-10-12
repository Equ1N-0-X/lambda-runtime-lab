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
        }
    }

    /// JSON構文・UTF-8・重複キーの不正を、本文全体の検証エラーとして返す。
    pub fn invalid_json() -> Self {
        Self::validation(vec![ErrorDetail::new("$", "invalid_json")])
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
        }
    }

    /// DynamoDBなど外部依存先の障害を502応答へ変換する。
    pub fn dependency() -> Self {
        Self {
            status: StatusCode::BAD_GATEWAY,
            content: ErrorContent {
                code: "DEPENDENCY_ERROR",
                message: "dependency error",
                details: Vec::new(),
            },
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
        }
    }

    pub fn status(&self) -> StatusCode {
        self.status
    }

    pub fn details(&self) -> &[ErrorDetail] {
        &self.content.details
    }
}

impl IntoResponse for AppError {
    /// 内部エラー型を、契約どおりのHTTP状態とJSON本文へ変換する。
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ErrorBody {
                error: self.content,
            }),
        )
            .into_response()
    }
}
