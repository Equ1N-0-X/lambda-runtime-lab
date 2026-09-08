resource "aws_lambda_function" "runtime" {
  for_each = local.runtimes

  function_name = each.value.lambda_name
  role          = aws_iam_role.lambda_execution.arn
  package_type  = "Image"
  image_uri     = each.value.image_uri

  memory_size   = local.common_lambda_settings.memory_size
  timeout       = local.common_lambda_settings.timeout
  architectures = local.common_lambda_settings.architectures

  environment {
    variables = local.common_lambda_settings.environment_variables
  }

  logging_config {
    log_format            = local.common_lambda_settings.logging.log_format
    application_log_level = local.common_lambda_settings.logging.application_log_level
    system_log_level      = local.common_lambda_settings.logging.system_log_level
  }

  depends_on = [
    aws_ecr_repository_policy.lambda_pull,
    aws_iam_role_policy.lambda_execution,
    aws_cloudwatch_log_group.lambda,
  ]

  lifecycle {
    precondition {
      condition = (
        each.value.image_uri != "" &&
        (
          startswith(each.value.image_uri, "${aws_ecr_repository.runtime[each.key].repository_url}:") ||
          startswith(each.value.image_uri, "${aws_ecr_repository.runtime[each.key].repository_url}@")
        )
      )
      error_message = "${each.key}_image_uri must be set to a tagged or digest URI from the Terraform-managed ${each.key} ECR repository before terraform plan or apply."
    }
  }
}
