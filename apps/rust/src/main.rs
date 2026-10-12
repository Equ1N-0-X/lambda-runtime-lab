//! Rust Lambdaの起動処理として、ログ・AWSクライアント・ルーターを組み立てる。

use std::{env, sync::Arc};

use aws_config::BehaviorVersion;
use rust_lambdalith::{
    AppState, build_router,
    repository::{DynamoDbItemRepository, ItemRepository},
};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), lambda_http::Error> {
    // 起動後の処理と初期化失敗を最初からJSONログへ出せるよう、他の初期化より先に設定する。
    init_logging();

    // テーブル名が未設定なら保存先を決められないため、Lambdaイベント待受けを開始せず終了する。
    let table_name = env::var("DYNAMODB_TABLE_NAME").map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "DYNAMODB_TABLE_NAMEの設定が必要です",
        )
    })?;

    // AWS標準設定からDynamoDBクライアントを作り、共有可能なリポジトリとしてルーターへ渡す。
    let aws_config = aws_config::defaults(BehaviorVersion::latest()).load().await;
    let repository: Arc<dyn ItemRepository> = Arc::new(DynamoDbItemRepository::new(
        aws_sdk_dynamodb::Client::new(&aws_config),
        table_name,
    ));
    let app = build_router(AppState { repository });

    lambda_http::run(app).await?;
    Ok(())
}

fn init_logging() {
    // RUST_LOGが有効ならそのフィルターを使い、未指定・不正ならアプリのINFO以上を既定にする。
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("rust_lambdalith=info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .json()
        .flatten_event(true)
        .with_current_span(false)
        .with_span_list(false)
        .init();
}
