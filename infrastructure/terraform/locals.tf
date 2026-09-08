locals {
  name_prefix        = "${var.project_name}-${var.environment}"
  log_retention_days = 14

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Workload    = "lambda-runtime-benchmark"
  }

  runtimes = {
    python = {
      image_uri       = var.python_image_uri
      repository_name = "${local.name_prefix}-python"
      lambda_name     = "${local.name_prefix}-python"
      api_name        = "${local.name_prefix}-python"
    }
    rust = {
      image_uri       = var.rust_image_uri
      repository_name = "${local.name_prefix}-rust"
      lambda_name     = "${local.name_prefix}-rust"
      api_name        = "${local.name_prefix}-rust"
    }
  }

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
