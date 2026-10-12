# 指定リージョンへ全リソースを作り、所有・環境・用途の共通タグを自動付与する。
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# ECRポリシーとARNを現在の認証先アカウント・パーティションに限定するため参照する。
data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}
