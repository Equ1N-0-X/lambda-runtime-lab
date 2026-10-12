# Python/RustそれぞれにIAM認証付きHTTP APIを作り、対応するLambdaへ全経路を転送する。
resource "aws_apigatewayv2_api" "runtime" {
  for_each = local.runtimes

  name          = each.value.api_name
  protocol_type = "HTTP"
}

# API GatewayからLambdaへpayload format 2.0で同期転送する統合を実装別に作る。
resource "aws_apigatewayv2_integration" "lambda_proxy" {
  for_each = local.runtimes

  api_id                 = aws_apigatewayv2_api.runtime[each.key].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.runtime[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = min(local.common_lambda_settings.timeout * 1000, 30000)
}

# すべてのAPI経路をLambda統合へ渡し、呼び出し元にはAWS IAM認証を要求する。
resource "aws_apigatewayv2_route" "lambda_proxy" {
  for_each = local.runtimes

  api_id             = aws_apigatewayv2_api.runtime[each.key].id
  route_key          = "$default"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.lambda_proxy[each.key].id}"
}

# 変更を自動反映する既定ステージを作り、アクセスログを実装別CloudWatchへ保存する。
resource "aws_apigatewayv2_stage" "default" {
  for_each = local.runtimes

  api_id      = aws_apigatewayv2_api.runtime[each.key].id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api[each.key].arn
    format          = local.api_access_log_format
  }
}

# 各API Gatewayだけが対応するLambdaを呼び出せるリソースベース権限を付与する。
resource "aws_lambda_permission" "api_gateway" {
  for_each = local.runtimes

  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.runtime[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.runtime[each.key].execution_arn}/*"
}
