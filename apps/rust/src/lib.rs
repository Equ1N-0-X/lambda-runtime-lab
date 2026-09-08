pub mod api;
pub mod domain;
pub mod error;
pub mod json;
pub mod model;
pub mod repository;
pub mod validation;

pub use api::{AppState, build_router};
