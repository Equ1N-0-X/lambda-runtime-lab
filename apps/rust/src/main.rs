use std::{env, sync::Arc};

use aws_config::BehaviorVersion;
use rust_lambdalith::{
    AppState, build_router,
    repository::{DynamoDbItemRepository, ItemRepository},
};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), lambda_http::Error> {
    init_logging();

    let table_name = env::var("DYNAMODB_TABLE_NAME").map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "DYNAMODB_TABLE_NAME must be set",
        )
    })?;
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
