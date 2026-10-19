# 出力は実装キー（spec/implementations.yaml）ごとの対応表にし、言語を足しても出力名を変えない。
output "api_endpoints" {
  description = "実装キーごとの、IAM認証で保護されたHTTP APIのエンドポイント。"
  value       = { for key, api in aws_apigatewayv2_api.runtime : key => api.api_endpoint }
}

output "lambda_names" {
  description = "実装キーごとのLambda関数名。"
  value       = { for key, function in aws_lambda_function.runtime : key => function.function_name }
}

output "ecr_repository_urls" {
  description = "実装キーごとのコンテナイメージのECRリポジトリURL。"
  value       = { for key, repository in aws_ecr_repository.runtime : key => repository.repository_url }
}

output "dynamodb_table_name" {
  description = "全実装が共有するベンチマーク用DynamoDBテーブル名。"
  value       = aws_dynamodb_table.benchmark.name
}
