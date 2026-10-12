//! HTTP入力を共通API契約のドメイン処理へ接続し、構造化ログを出力する。
//!
//! フレームワーク固有のリクエスト形式・エラーをここで正規化し、Python実装と同じ応答と
//! ベンチマーク相関項目を提供する。

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
const BENCHMARK_REQUEST_INDEX_HEADER: &str = "x-benchmark-request-index";
#[derive(Clone)]
pub struct AppState {
    pub repository: Arc<dyn ItemRepository>,
}

#[derive(Clone)]
struct RequestProfile(String);

pub fn build_router(state: AppState) -> Router {
    // 五つの公開APIと未定義経路の応答を登録し、最後に全リクエストのログミドルウェアを重ねる。
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
    // 計測IDだけを検証し、外部I/OなしでLambdaの到達性を返す。
    let result = check_benchmark_run_id(&headers).map(|()| Json(HealthResponse { status: "ok" }));
    profiled(result, "not_applicable")
}

async fn transform(headers: HeaderMap, body: Bytes) -> Response {
    // 本文のJSON解析、構造検証、業務検証をこの順で行い、成功時だけレコードを正規化する。
    let result = (|| {
        check_benchmark_run_id(&headers)?;
        let request = validation::transform_request(&parse_body(&body)?)?;
        validation::transform_semantics(&request)?;
        Ok(Json(domain::transform(request)))
    })();
    profiled(result, "not_applicable")
}

async fn compute(headers: HeaderMap, body: Bytes) -> Response {
    // 検証に成功した計算プロファイルをログ用に保存し、指定されたCPU計算を実行する。
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
    // パスIDを検証してから一件だけ取得し、保存先障害と未登録をドメイン層でHTTPエラーへ変換する。
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
    // 本文に指定されたプロファイルに従い、直列または並行の一回ずつの読み取りを選ぶ。
    let mut profile = String::from("unknown");
    let result = async {
        check_benchmark_run_id(&headers)?;
        let request = validation::aggregate_request(&parse_body(&body)?)?;
        profile = request.profile.clone();
        validation::aggregate_semantics(&request)?;

        // profile が sequential の場合はID順に読み、concurrent の場合は全IDを並行して読み取る。
        // どちらもIDごとの読み取り回数は一回に固定し、比較条件を揃える。
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
    // 登録済み経路と一致しない場合は、契約で定めた未検出エラーを返す。
    AppError::route_not_found()
}

fn parse_body(body: &[u8]) -> Result<serde_json::Value, AppError> {
    // 構文、UTF-8、重複キーのいずれかが不正なら、本文全体のinvalid_jsonとして扱う。
    json::parse(body).ok_or_else(AppError::invalid_json)
}

/// 本文を読む前に計測相関ヘッダーを検証し、Python実装と同じ検証順序を守る。
fn check_benchmark_run_id(headers: &HeaderMap) -> Result<(), AppError> {
    // ヘッダーが未指定の場合は通常リクエストとして通し、指定されている場合だけ文字列として検証する。
    let Some(value) = headers.get(BENCHMARK_RUN_ID_HEADER) else {
        return Ok(());
    };

    // ヘッダーがUTF-8文字列ならID規則を検証し、変換不能なバイト列ならpattern違反として返す。
    match value.to_str() {
        Ok(run_id) => validation::benchmark_run_id(run_id),
        Err(_) => Err(AppError::validation(vec![ErrorDetail::new(
            validation::BENCHMARK_RUN_ID_FIELD,
            "pattern",
        )])),
    }
}

/// ランナーが付与した計測番号をログ相関用に正規化し、通常リクエストや不正値は0とする。
fn benchmark_request_index(headers: &HeaderMap) -> i64 {
    headers
        .get(BENCHMARK_REQUEST_INDEX_HEADER)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<i64>().ok())
        .filter(|value| *value != 0)
        .unwrap_or_default()
}

fn profiled<T: IntoResponse>(result: Result<T, AppError>, profile: &str) -> Response {
    // 成功・失敗をHTTP応答へ変換した後も、ログミドルウェアがプロファイルを取得できるよう拡張へ保存する。
    // resultが成功なら値、失敗ならAppErrorをそれぞれHTTP応答へ変換する。
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
    // 受信時にしか参照できないヘッダー、Lambdaコンテキスト、経路を、応答後のログ出力用に退避する。
    let benchmark_run_id = request
        .headers()
        .get(BENCHMARK_RUN_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let benchmark_request_index = benchmark_request_index(request.headers());
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

    let status_code = response.status().as_u16();

    // 502は外部依存先の障害としてWARN、500以上（502以外）は内部障害としてERROR、
    // それ以外は仕様どおり応答できた処理としてINFOに分類する。全分類で相関項目は共通に保つ。
    match status_code {
        502 => tracing::warn!(
            implementation = "rust",
            lambda_request_id,
            benchmark_run_id,
            benchmark_request_index,
            route,
            profile,
            status_code,
            "外部データストアへのアクセスに失敗しました"
        ),
        500..=599 => tracing::error!(
            implementation = "rust",
            lambda_request_id,
            benchmark_run_id,
            benchmark_request_index,
            route,
            profile,
            status_code,
            "リクエスト処理中に内部エラーが発生しました"
        ),
        _ => tracing::info!(
            implementation = "rust",
            lambda_request_id,
            benchmark_run_id,
            benchmark_request_index,
            route,
            profile,
            status_code,
            "リクエスト処理が完了しました"
        ),
    }

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
    fn benchmark_request_indices_distinguish_measurement_and_warmup() {
        let measured = HeaderMap::from_iter([(
            BENCHMARK_REQUEST_INDEX_HEADER.parse().unwrap(),
            "7".parse().unwrap(),
        )]);
        let warmup = HeaderMap::from_iter([(
            BENCHMARK_REQUEST_INDEX_HEADER.parse().unwrap(),
            "-3".parse().unwrap(),
        )]);
        let invalid = HeaderMap::from_iter([(
            BENCHMARK_REQUEST_INDEX_HEADER.parse().unwrap(),
            "invalid".parse().unwrap(),
        )]);

        assert_eq!(benchmark_request_index(&measured), 7);
        assert_eq!(benchmark_request_index(&warmup), -3);
        assert_eq!(benchmark_request_index(&invalid), 0);
        assert_eq!(benchmark_request_index(&HeaderMap::new()), 0);
    }

    #[test]
    fn malformed_bodies_report_only_the_root_field() {
        let error = parse_body(br#"{"items":"#).unwrap_err();

        assert_eq!(error.details(), [ErrorDetail::new("$", "invalid_json")]);
    }
}
