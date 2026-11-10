# 実装ごとに暗号化・push時スキャン・タグ不変のECRリポジトリを作る。
resource "aws_ecr_repository" "runtime" {
  for_each = local.runtimes

  name                 = each.value.repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# 同一アカウントかつ本プロジェクト名のLambdaだけに、イメージレイヤーの取得を許可する。
resource "aws_ecr_repository_policy" "lambda_pull" {
  for_each = aws_ecr_repository.runtime

  repository = each.value.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLambdaImageRetrieval"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Condition = {
          StringLike = {
            "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-*"
          }
        }
      },
    ]
  })
}

# 使わなくなった古いイメージを、ECRが自動で削除する（評価は24時間以内に行われる）。
# Lambdaが使用中のイメージを消すと関数が Failed になるため、make publish-images で登録した
# イメージは言語ごとに新しい3つまで残す（デプロイ中の版・切り戻し用の直前の版・未デプロイの最新版）。
# 手で付けた benchmark- 以外のタグは対象にせず、削除しない。
resource "aws_ecr_lifecycle_policy" "runtime" {
  for_each = aws_ecr_repository.runtime

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "make publish-images で登録したイメージは新しい3つだけ残す"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["benchmark-"]
          countType     = "imageCountMoreThan"
          countNumber   = local.retained_image_count
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "タグのないイメージは登録から1日で削除する"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
    ]
  })
}
