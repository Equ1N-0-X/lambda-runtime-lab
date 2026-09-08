output "python_api_endpoint" {
  description = "IAM-protected Python HTTP API endpoint."
  value       = aws_apigatewayv2_api.runtime["python"].api_endpoint
}

output "rust_api_endpoint" {
  description = "IAM-protected Rust HTTP API endpoint."
  value       = aws_apigatewayv2_api.runtime["rust"].api_endpoint
}

output "python_lambda_name" {
  description = "Python Lambda function name."
  value       = aws_lambda_function.runtime["python"].function_name
}

output "rust_lambda_name" {
  description = "Rust Lambda function name."
  value       = aws_lambda_function.runtime["rust"].function_name
}

output "dynamodb_table_name" {
  description = "Shared benchmark DynamoDB table name."
  value       = aws_dynamodb_table.benchmark.name
}

output "python_ecr_repository_url" {
  description = "Python container image repository URL."
  value       = aws_ecr_repository.runtime["python"].repository_url
}

output "rust_ecr_repository_url" {
  description = "Rust container image repository URL."
  value       = aws_ecr_repository.runtime["rust"].repository_url
}
