# 両実装が同じ項目を強整合読み取りし、外部I/O条件を揃える共有DynamoDBテーブルを作る。
resource "aws_dynamodb_table" "benchmark" {
  name         = "${local.name_prefix}-items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}
