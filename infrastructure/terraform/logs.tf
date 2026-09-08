resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.runtimes

  name              = "/aws/lambda/${each.value.lambda_name}"
  retention_in_days = local.log_retention_days
}

resource "aws_cloudwatch_log_group" "api" {
  for_each = local.runtimes

  name              = "/aws/apigateway/${each.value.api_name}"
  retention_in_days = local.log_retention_days
}
