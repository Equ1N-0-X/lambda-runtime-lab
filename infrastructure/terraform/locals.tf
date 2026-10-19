locals {
  # 全リソースで共有する名前接頭辞とログ保持期間を一箇所で管理する。
  name_prefix        = "${var.project_name}-${var.environment}"
  log_retention_days = 14

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Workload    = "lambda-runtime-benchmark"
  }

  # 比較対象の言語は spec/implementations.yaml を正本とし、ここで重複定義しない。
  implementations = yamldecode(file("${path.module}/../../spec/implementations.yaml")).implementations

  # 言語ごとに変えてよい識別子とイメージURIだけを実装別に定義する。
  runtimes = {
    for key, _ in local.implementations : key => {
      image_uri       = lookup(var.image_uris, key, "")
      repository_name = "${local.name_prefix}-${key}"
      lambda_name     = "${local.name_prefix}-${key}"
      api_name        = "${local.name_prefix}-${key}"
    }
  }

  # 比較の公平性に関わるLambda設定とログ水準は全実装で共有する。
  common_lambda_settings = {
    memory_size   = var.lambda_memory_mb
    timeout       = var.lambda_timeout_seconds
    architectures = [var.architecture]
    environment_variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.benchmark.name
      ENVIRONMENT         = var.environment
    }
    logging = {
      log_format            = "JSON"
      application_log_level = "INFO"
      system_log_level      = "INFO"
    }
  }

  # APIアクセスログは相関・失敗調査に必要な項目だけをJSONで記録する。
  api_access_log_format = jsonencode({
    request_id        = "$context.requestId"
    request_time      = "$context.requestTime"
    http_method       = "$context.httpMethod"
    route_key         = "$context.routeKey"
    status            = "$context.status"
    response_length   = "$context.responseLength"
    integration_error = "$context.integrationErrorMessage"
  })
}
