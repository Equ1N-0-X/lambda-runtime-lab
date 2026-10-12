output "python_api_endpoint" {
  description = "IAM認証で保護されたPython HTTP APIのエンドポイント。"
  value       = aws_apigatewayv2_api.runtime["python"].api_endpoint
}

output "rust_api_endpoint" {
  description = "IAM認証で保護されたRust HTTP APIのエンドポイント。"
  value       = aws_apigatewayv2_api.runtime["rust"].api_endpoint
}

output "python_lambda_name" {
  description = "Python Lambda関数名。"
  value       = aws_lambda_function.runtime["python"].function_name
}

output "rust_lambda_name" {
  description = "Rust Lambda関数名。"
  value       = aws_lambda_function.runtime["rust"].function_name
}

output "dynamodb_table_name" {
  description = "両実装が共有するベンチマーク用DynamoDBテーブル名。"
  value       = aws_dynamodb_table.benchmark.name
}

output "python_ecr_repository_url" {
  description = "PythonコンテナイメージのECRリポジトリURL。"
  value       = aws_ecr_repository.runtime["python"].repository_url
}

output "rust_ecr_repository_url" {
  description = "RustコンテナイメージのECRリポジトリURL。"
  value       = aws_ecr_repository.runtime["rust"].repository_url
}
