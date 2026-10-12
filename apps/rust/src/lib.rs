//! Rust Lambda実装の公開モジュールと、アプリ組み立てに必要な型を公開する。

pub mod api;
pub mod domain;
pub mod error;
pub mod json;
pub mod model;
pub mod repository;
pub mod validation;

pub use api::{AppState, build_router};
