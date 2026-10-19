# AWSリージョン名の形式を検証し、全ベンチマーク資源の配置先を決める。
variable "aws_region" {
  description = "ベンチマーク用リソースを作成するAWSリージョン。"
  type        = string
  default     = "ap-northeast-1"

  validation {
    # 一般・GovCloudのリージョン命名形式に一致する文字列だけを許可する。
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_regionには有効なAWSリージョン名を指定してください。"
  }
}

# AWSリソース名の接頭辞として使える、小文字のプロジェクト識別子を受け取る。
variable "project_name" {
  description = "リソース名の接頭辞に使う小文字のプロジェクト識別子。"
  type        = string
  default     = "lambda-runtime-lab"

  validation {
    # 英字開始、英数字終了、全体3〜30文字の小文字英数字・ハイフンだけを許可する。
    condition     = can(regex("^[a-z][a-z0-9-]{1,28}[a-z0-9]$", var.project_name))
    error_message = "project_nameは英字で始まる3〜30文字の小文字英数字またはハイフンで指定してください。"
  }
}

# 全言語のLambdaへ同じ値を設定し、メモリ条件を揃える。
variable "lambda_memory_mb" {
  description = "全言語のLambdaへ同じ値を割り当てるメモリ量。"
  type        = number
  default     = 512

  validation {
    # Lambdaが許可する128〜10240MBの整数だけを受け付ける。
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 10240 && floor(var.lambda_memory_mb) == var.lambda_memory_mb
    error_message = "lambda_memory_mbは128以上10240以下の整数で指定してください。"
  }
}

# 全言語のLambdaへ同じ値を設定し、タイムアウト条件を揃える。
variable "lambda_timeout_seconds" {
  description = "全言語のLambdaへ同じ値を設定するタイムアウト秒数。"
  type        = number
  default     = 30

  validation {
    # Lambdaが許可する1〜900秒の整数だけを受け付ける。
    condition     = var.lambda_timeout_seconds >= 1 && var.lambda_timeout_seconds <= 900 && floor(var.lambda_timeout_seconds) == var.lambda_timeout_seconds
    error_message = "lambda_timeout_secondsは1以上900以下の整数で指定してください。"
  }
}

# 全言語のLambdaとコンテナビルドで共有するCPUアーキテクチャを選ぶ。
variable "architecture" {
  description = "全言語のLambda関数で使用する命令セットアーキテクチャ。"
  type        = string
  default     = "arm64"

  validation {
    # Lambdaがコンテナで対応するarm64またはx86_64だけを許可する。
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architectureはarm64またはx86_64で指定してください。"
  }
}

# 実装キー（spec/implementations.yaml）ごとのECRイメージURI。
# scripts/publish-images.sh が images.auto.tfvars.json へ書き出し、make deploy が自動で読み込む。
variable "image_uris" {
  description = "実装キーからLambda用ECRイメージURIへの対応表。validateでは空を許可し、plan/applyでは全実装の管理対象ECRのURIを要求する。"
  type        = map(string)
  default     = {}

  validation {
    # 各値がタグかsha256 digest付きの完全なprivate ECR URIである場合だけ許可する。
    condition = alltrue([
      for uri in values(var.image_uris) :
      can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com(\\.cn)?/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|@sha256:[0-9a-f]{64})$", uri))
    ])
    error_message = "image_urisの各値は、タグかsha256 digest付きprivate ECR URIで指定してください。"
  }
}

variable "environment" {
  description = "リソース名とLambda環境変数へ含める小文字の環境識別子。"
  type        = string
  default     = "dev"

  validation {
    # 英字開始、英数字終了、全体2〜12文字の小文字英数字・ハイフンだけを許可する。
    condition     = can(regex("^[a-z][a-z0-9-]{0,10}[a-z0-9]$", var.environment))
    error_message = "environmentは英字で始まる2〜12文字の小文字英数字またはハイフンで指定してください。"
  }
}
