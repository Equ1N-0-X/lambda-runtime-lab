resource "aws_apigatewayv2_api" "runtime" {
  for_each = local.runtimes

  name          = each.value.api_name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_proxy" {
  for_each = local.runtimes

  api_id                 = aws_apigatewayv2_api.runtime[each.key].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.runtime[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = min(local.common_lambda_settings.timeout * 1000, 30000)
}

resource "aws_apigatewayv2_route" "lambda_proxy" {
  for_each = local.runtimes

  api_id             = aws_apigatewayv2_api.runtime[each.key].id
  route_key          = "$default"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.lambda_proxy[each.key].id}"
}

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

resource "aws_lambda_permission" "api_gateway" {
  for_each = local.runtimes

  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.runtime[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.runtime[each.key].execution_arn}/*"
}
