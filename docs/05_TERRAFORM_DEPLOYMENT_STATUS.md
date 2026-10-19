# Terraform デプロイ状況

## 目的と確認時点

この文書は、Terraform の定義、ローカル state、および AWS の読み取り照会を突き合わせ、現時点で作成済みの AWS リソースと未作成のリソースを明確にするものです。

- 確認日時: 2026-09-15（JST）
- 対象リージョン: `ap-northeast-1`
- 正本: `infrastructure/terraform/` の Terraform 定義と `terraform.tfstate`
- 実環境の確認方法: AWS CLI による読み取り専用の照会

## 結論

Terraform 管理対象の 23 リソースインスタンスが作成済みです。Python／Rust のコンテナ Lambda、HTTP API、共有 DynamoDB、IAM、CloudWatch Logs、ECR が稼働しています。

両 API の `/health` へ AWS IAM（SigV4）署名付きで各 1 回リクエストし、いずれも HTTP 200 を確認しました。匿名アクセスは許可していません。

## 作成済みのコンポーネント

| コンポーネント | 数 | Terraform 定義 | 実環境の状態 |
| --- | ---: | --- | --- |
| Python 用 ECR リポジトリ | 1 | `ecr.tf` の `aws_ecr_repository.runtime["python"]` | 作成済み。イメージあり。 |
| Rust 用 ECR リポジトリ | 1 | `ecr.tf` の `aws_ecr_repository.runtime["rust"]` | 作成済み。イメージあり。 |
| Lambda による ECR イメージ取得ポリシー | 2 | `ecr.tf` の `aws_ecr_repository_policy.lambda_pull` | 両リポジトリに適用済み。 |
| Lambda 実行ロールとポリシー | 1 + 1 | `iam.tf` | DynamoDB 読み取り・CloudWatch Logs 書き込み権限で作成済み。 |
| DynamoDB テーブル | 1 | `dynamodb.tf` | `lambda-runtime-lab-benchmark-items` を作成済み。 |
| Lambda／API Gateway のロググループ | 2 + 2 | `logs.tf` | 作成済み。 |
| Python／Rust Lambda 関数 | 2 | `lambda.tf` | `arm64`、稼働中。 |
| HTTP API、統合、ルート、ステージ、呼び出し権限 | 各 2 | `apigateway.tf` | 作成済み。`$default` は `AWS_IAM` 認可。 |

Lambda が使用中の最新イメージは `benchmark-20260915T151500Z` タグで公開した単一の OCI イメージマニフェストです。Python は `sha256:80decd0f…`、Rust は `sha256:1cee1abd…` を参照しています。

## 未作成のコンポーネント

Terraform 定義に含まれるコンポーネントに未作成のものはありません。データ投入と本番相当のベンチマーク実行は、意図的に別コマンドのままです。

## Terraform ファイルと責務の対応

| ファイル | 責務 | 現在の状態 |
| --- | --- | --- |
| `versions.tf` | Terraform と AWS Provider のバージョン制約 | 設定のみ |
| `providers.tf` | AWS Provider、アカウント・パーティションのデータ取得 | データソースのみ state に記録 |
| `variables.tf` | リージョン、環境名、メモリ、タイムアウト、アーキテクチャ、イメージ URI | 設定値の定義のみ |
| `locals.tf` | 命名規則、Python/Rust の比較対象、共通 Lambda 設定、ログ形式 | 設定の組み立てのみ |
| `ecr.tf` | ECR リポジトリと Lambda のイメージ取得ポリシー | 作成済み |
| `iam.tf` | 共通 Lambda 実行ロール、DynamoDB 読み取り・ログ書き込み権限 | 作成済み |
| `dynamodb.tf` | 共有ベンチマークテーブル | 作成済み |
| `logs.tf` | Lambda と API Gateway のロググループ | 作成済み |
| `lambda.tf` | Python／Rust のコンテナイメージ Lambda | 作成済み |
| `apigateway.tf` | HTTP API、Lambda 統合、IAM 認可、ステージ、アクセスログ、呼び出し権限 | 作成済み |
| `outputs.tf` | API URL、Lambda 名、テーブル名、ECR URL の出力 | すべて取得可能 |

## 依存関係と現在地

```text
ローカル Docker ビルド
        |
        v
ECR（Python / Rust）             ← 作成済み・最新イメージ公開済み
        |
        v
Lambda（Python / Rust）          ← 作成済み・稼働中
        |                \
        |                 +--> CloudWatch Logs  ← 作成済み
        v
DynamoDB                         ← 作成済み

API Gateway HTTP API             ← 作成済み（AWS_IAM 認可）
        |
        v
Lambda を AWS_IAM 認可で呼び出す
```

ECR を先に作成した理由は、Lambda を作成するには ECR 上のイメージ URI が必要だからです。この順序でのデプロイは完了しています。

## 次に必要なこと

次の作業は、DynamoDB へのシード投入後にベンチマークを実行することです。通常のイメージ更新では、以下の手順を使用します。

1. `make bootstrap-ecr` で ECR リポジトリとイメージ取得ポリシーだけを作成する。
2. `make publish-images` でローカルビルド・ECR プッシュを実行し、digest URI を `images.auto.tfvars.json` へ書き出す。
3. Terraform plan を確認してから `make deploy` を実行する。

現在の API URL は、Python が `https://clwf5l9sol.execute-api.ap-northeast-1.amazonaws.com`、Rust が `https://b3wszko32b.execute-api.ap-northeast-1.amazonaws.com` です。

## 未決事項

`make bootstrap-ecr` は Lambda と ECR の作成順序を解決するため、ECR 関連リソースだけを Terraform の対象指定で作成します。通常のインフラ更新には使用せず、`make deploy` を使用してください。
