use std::sync::Arc;

use axum::{
    Json, Router,
    body::Bytes,
    extract::{MatchedPath, Path, Request, State},
    http::HeaderMap,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use lambda_http::RequestExt;

use crate::{
    domain,
    error::{AppError, ErrorDetail},
    json,
    model::{HealthResponse, ItemResponse},
    repository::ItemRepository,
    validation,
};

const BENCHMARK_RUN_ID_HEADER: &str = "x-benchmark-run-id";
#[derive(Clone)]
pub struct AppState {
    pub repository: Arc<dyn ItemRepository>,
}

#[derive(Clone)]
struct RequestProfile(String);

pub fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/transform", post(transform))
        .route("/compute", post(compute))
        .route("/items/{id}", get(item))
        .route("/aggregate", post(aggregate))
        .fallback(route_not_found)
        .with_state(state)
        .layer(middleware::from_fn(log_request))
}

async fn health(headers: HeaderMap) -> Response {
    let result = check_benchmark_run_id(&headers).map(|()| Json(HealthResponse { status: "ok" }));
    profiled(result, "not_applicable")
}

async fn transform(headers: HeaderMap, body: Bytes) -> Response {
    let result = (|| {
        check_benchmark_run_id(&headers)?;
        let request = validation::transform_request(&parse_body(&body)?)?;
        validation::transform_semantics(&request)?;
        Ok(Json(domain::transform(request)))
    })();
    profiled(result, "not_applicable")
}

async fn compute(headers: HeaderMap, body: Bytes) -> Response {
    let mut profile = String::from("unknown");
    let result = (|| {
        check_benchmark_run_id(&headers)?;
        let request = validation::compute_request(&parse_body(&body)?)?;
        profile = request.profile.clone();
        validation::compute_semantics(&request)?;
        Ok(Json(domain::compute(&request)))
    })();
    profiled(result, &profile)
}

async fn item(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    let result = async {
        check_benchmark_run_id(&headers)?;
        validation::item_id(&id)?;
        let item = domain::get_item(state.repository.as_ref(), &id).await?;
        Ok(Json(ItemResponse { item }))
    }
    .await;
    profiled(result, "not_applicable")
}

async fn aggregate(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let mut profile = String::from("unknown");
    let result = async {
        check_benchmark_run_id(&headers)?;
        let request = validation::aggregate_request(&parse_body(&body)?)?;
        profile = request.profile.clone();
        validation::aggregate_semantics(&request)?;

        let response = if request.profile == "sequential" {
            domain::aggregate_sequential(state.repository.as_ref(), &request.ids).await?
        } else {
            domain::aggregate_concurrent(Arc::clone(&state.repository), request.ids).await?
        };
        Ok(Json(response))
    }
    .await;
    profiled(result, &profile)
}

async fn route_not_found() -> AppError {
    AppError::route_not_found()
}

fn parse_body(body: &[u8]) -> Result<serde_json::Value, AppError> {
    json::parse(body).ok_or_else(AppError::invalid_json)
}

/// Rejects a malformed correlation header before the body is decoded, matching
/// the order the Python implementation applies.
fn check_benchmark_run_id(headers: &HeaderMap) -> Result<(), AppError> {
    let Some(value) = headers.get(BENCHMARK_RUN_ID_HEADER) else {
        return Ok(());
    };
    match value.to_str() {
        Ok(run_id) => validation::benchmark_run_id(run_id),
        Err(_) => Err(AppError::validation(vec![ErrorDetail::new(
            validation::BENCHMARK_RUN_ID_FIELD,
            "pattern",
        )])),
    }
}

fn profiled<T: IntoResponse>(result: Result<T, AppError>, profile: &str) -> Response {
    let mut response = match result {
        Ok(value) => value.into_response(),
        Err(error) => error.into_response(),
    };
    response
        .extensions_mut()
        .insert(RequestProfile(profile.to_owned()));
    response
}

async fn log_request(request: Request, next: Next) -> Response {
    let benchmark_run_id = request
        .headers()
        .get(BENCHMARK_RUN_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let lambda_request_id = request
        .lambda_context_ref()
        .map(|context| context.request_id.clone())
        .unwrap_or_default();
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str)
        .unwrap_or_else(|| request.uri().path())
        .to_owned();

    let response = next.run(request).await;
    let profile = response
        .extensions()
        .get::<RequestProfile>()
        .map(|profile| profile.0.as_str())
        .unwrap_or("unknown");

    tracing::info!(
        implementation = "rust",
        lambda_request_id,
        benchmark_run_id,
        route,
        profile,
        status_code = response.status().as_u16(),
        "request completed"
    );
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn benchmark_run_ids_accept_runner_identifiers() {
        let uuid = HeaderMap::from_iter([(
            "x-benchmark-run-id".parse().unwrap(),
            "3f2504e0-4f89-11d3-9a0c-0305e82c3301".parse().unwrap(),
        )]);
        let readable = HeaderMap::from_iter([(
            "x-benchmark-run-id".parse().unwrap(),
            "arm64-512-health-20260101T000000Z".parse().unwrap(),
        )]);
        let invalid = HeaderMap::from_iter([(
            "x-benchmark-run-id".parse().unwrap(),
            "-not a run id".parse().unwrap(),
        )]);

        assert!(check_benchmark_run_id(&uuid).is_ok());
        assert!(check_benchmark_run_id(&readable).is_ok());
        assert!(check_benchmark_run_id(&HeaderMap::new()).is_ok());
        assert_eq!(
            check_benchmark_run_id(&invalid).unwrap_err().details(),
            [ErrorDetail::new("headers.X-Benchmark-Run-Id", "pattern")]
        );
    }

    #[test]
    fn malformed_bodies_report_only_the_root_field() {
        let error = parse_body(br#"{"items":"#).unwrap_err();

        assert_eq!(error.details(), [ErrorDetail::new("$", "invalid_json")]);
    }
}
