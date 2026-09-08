//! Canonical error taxonomy.
//!
//! Every code, message, status, and detail string here is fixed by
//! `spec/openapi.yaml` so that the Python implementation can return
//! byte-identical error bodies.

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

    pub fn invalid_json() -> Self {
        Self::validation(vec![ErrorDetail::new("$", "invalid_json")])
    }

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
