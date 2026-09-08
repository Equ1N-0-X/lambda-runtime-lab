resource "aws_iam_role" "lambda_execution" {
  name = "${local.name_prefix}-lambda-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LambdaAssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy" "lambda_execution" {
  name = "${local.name_prefix}-lambda-execution"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBenchmarkItems"
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = aws_dynamodb_table.benchmark.arn
      },
      {
        Sid    = "WriteLambdaLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          for log_group in aws_cloudwatch_log_group.lambda :
          "${log_group.arn}:*"
        ]
      },
    ]
  })
}
