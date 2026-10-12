# Lambdaアプリケーション・システムログを実装別に14日間保持する。
resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.runtimes

  name              = "/aws/lambda/${each.value.lambda_name}"
  retention_in_days = local.log_retention_days
}

# API Gatewayアクセスログを実装別に保持し、入口側の状態・統合エラーを調査可能にする。
resource "aws_cloudwatch_log_group" "api" {
  for_each = local.runtimes

  name              = "/aws/apigateway/${each.value.api_name}"
  retention_in_days = local.log_retention_days
}
