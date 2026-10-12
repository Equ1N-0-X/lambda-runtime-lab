# Python と Rust による AWS Lambda Lambdalith ベンチマーク

## 概要

このリポジトリは、AWS Lambda のコンテナイメージとしてデプロイする小規模 API アプリケーションを、Python と Rust で比較可能にするための再現可能な検証環境です。どちらの実装も単一の Lambda 関数から同じ 5 つのルートを公開し、同じ DynamoDB データを使用します。特定の言語が常に優れているとは前提せず、ランタイムの挙動、リソース使用量、デプロイ成果物、推定クラウドコストを測定します。

## 目的

言語ベンチマークでは、アプリケーションを実際にリリースする際の意思決定を十分に表せないことが多くあります。本プロジェクトでは、小規模なサーバーレス API の技術選定に影響するほど、ランタイムと実装の差がどこで現れるかを検証します。外部から観測できる挙動と実験条件はそろえつつ、各言語で一般的なフレームワーク、並行処理モデル、バリデーション、シリアライズ、リソースライフサイクルを使用できるようにしています。

## このリポジトリにおける Lambdalith

ここでいう Lambdalith は、複数のルートとアプリケーションロジックを 1 つの Lambda 関数にまとめた小規模な Web/API アプリケーションです。大規模なモノリスを意図するものではありません。ルーティング、シリアライズ、CPU 処理、DynamoDB アクセスを 1 つのデプロイ可能な単位に収め、アプリケーションスタックとしてこれらの特性を比較します。

## 比較方針

基本原則は、**WHAT は同一、HOW は各言語で自然に**です。

2 つのアプリケーションは、正本となる OpenAPI 契約、リクエストとレスポンスのデータ、バリデーションとエラー条件、決定的な計算、DynamoDB の操作回数、データセット、Lambda のメモリとタイムアウト、対象アーキテクチャ、ベンチマークワークロード設定を共有します。`spec/openapi.yaml`、`spec/workloads.yaml`、`spec/testcases/` が正本の入力です。

Python では FastAPI、Pydantic、`asyncio`、モジュールレベルでのリソース再利用を、Rust では Axum、Serde、Tokio、アプリケーション状態を使用できます。内部の呼び出しグラフを一致させる必要はありません。共通の Contract Test は、正本の各ケースを稼働中の両 API に送信し、ステータスと完全な JSON 結果を比較します。JSON オブジェクトのキー順は無視しますが、配列順と数値型は保持し、非決定的なフィールドとして明示的に宣言した JSON Pointer のみを除外します。そのため、チェックサムと共通エラーレスポンスも厳密な比較対象です。

## アーキテクチャ

```text
                           ベンチマークランナー
                    （AWS 認証情報チェーン + SigV4）
                                  |
                    +-------------+-------------+
                    |                           |
             API Gateway HTTP API       API Gateway HTTP API
                 （AWS_IAM）                 （AWS_IAM）
                    |                           |
           Python Lambdalith             Rust Lambdalith
                    |                           |
                    +----------+----------------+
                               |
                           DynamoDB

             Lambda ログと REPORT レコード -> CloudWatch
             コンテナイメージ                 -> Amazon ECR
             インフラ                         -> Terraform
             生データ / レポート              -> JSON、CSV、Markdown
```

両 Lambda 関数は、同等の API Gateway 統合、IAM 権限、ログ、メモリ、タイムアウト、環境、アーキテクチャ、および同一の DynamoDB テーブルを使用します。AWS リソースは Terraform が管理します。ベンチマークランナーは関数の外部にあり、同一のクライアント側条件で両方の対象を呼び出します。

## 技術スタック

- Python: CPython、FastAPI、Mangum、Pydantic、AWS SDK、pytest、Ruff、uv
- Rust: stable Rust、Axum、`lambda_http`、Tokio、Serde、AWS SDK for Rust、rustfmt、Clippy
- ランタイムパッケージング: マルチステージ Docker ビルドと、AL2023 Lambda OS-only の最終ベースイメージ
- インフラ: AWS Lambda、API Gateway HTTP API、ECR、DynamoDB、CloudWatch、IAM、Terraform
- ベンチマーク: Python ランナー、決定的な YAML ワークロード、生の JSON/JSONL レコード、CSV/Markdown レポート
- CI: アプリケーション、契約、arm64 コンテナ、Terraform の検証を行う GitHub Actions

## API エンドポイント

- `GET /health`: 外部 I/O を伴わないルーター、フレームワーク、ランタイムのオーバーヘッドを測定します。
- `POST /transform`: JSON のデコード、バリデーション、正規化、並べ替え、エンコードを測定します。
- `POST /compute`: 決定的な CPU バウンドのハッシュ処理を実行します。MVP では共通の `sequential` プロファイルのみ有効です。
- `GET /items/{id}`: DynamoDB の項目を 1 件読み取り、共通の未検出エラーを返します。
- `POST /aggregate`: `sequential` または `concurrent` モードで同等の DynamoDB 読み取りを行い、項目 ID で正規化した件数、合計、決定的なチェックサムを返します。

レスポンスボディから実装言語を識別できることはありません。エラーは共通の `error.code`、`error.message`、任意の `error.details` スキーマを使用します。

## ベンチマークワークロード

`spec/workloads.yaml` は、次の制御されたワークロードを定義します。

- `health`
- `transform-small`
- `transform-large`
- `compute-sequential`
- `items`
- `aggregate-sequential`
- `aggregate-concurrent`

各定義には、メソッド、パス、ペイロード、ウォームアップ回数、測定反復回数、クライアント並行数を記録します。標準マトリクスでは arm64 と、等しい Lambda メモリ設定である 256 MB、512 MB、1024 MB を使用します。CPU の並列処理と x86_64 は MVP の対象外です。

## コンテナ方針

両方の最終イメージには、同じ AL2023 Lambda OS-only ベースを使用します。`scripts/build-images.sh` はそのベースのダイジェストを一度だけ解決して両方のビルドに渡すため、比較実行中に `al2023` タグの異なるリビジョンを取得することはありません。解決したダイジェストはビルドメトリクスとともに記録します。言語固有のビルダーステージは意図的に異なります。Python の最終ステージには CPython、Lambda Runtime Interface Client、ランタイム依存関係、アプリケーションコードを含めます。Rust の最終ステージにはリリースバイナリと必要なランタイムファイルを含め、Cargo やコンパイラの成果物は含めません。また、Rust のビルダーステージも Amazon Linux 2023 で動作させます。これにより、動的リンクされたバイナリが実行環境の glibc と一致し、アロケータの挙動を変えてしまう静的 musl ビルドを回避します。

既定のビルド対象は `linux/arm64` です。記録する成果物データには、ビルド時間、ローカルイメージのサイズとレイヤー、ECR のサイズとダイジェスト、Python のランタイム・依存関係・アプリケーションの使用量、Rust バイナリのサイズを含みます。

**コンテナイメージのサイズはデプロイ使用量の指標であり、Lambda 実行料金ではありません。** 実行コストは、課金対象時間、設定メモリ、リクエスト料金、設定アーキテクチャの料金から計算します。

## メトリクス

ランナーと分析器は、ビルド／成果物の観測値とランタイムの観測値を分けて扱います。

- クライアント側レイテンシーと P50、P95、P99
- Lambda Duration と Billed Duration
- Init Duration、Max Memory Used、設定メモリ、エラー数、エラー率
- コールド／ウォームサンプル数
- ビルド時間、イメージ／レイヤーサイズ、ECR ダイジェストとサイズ、言語固有の使用量
- リクエストあたり、ワークロードあたり、100 万リクエストあたりの推定コスト
- Git リビジョン、実行 ID、リージョン、アーキテクチャ、メモリ、ワークロード、イメージダイジェスト

欠損した CloudWatch 値は `null` のままとし、ゼロには変換しません。

## コスト計算

料金はアプリケーションコードに散在させる定数ではなく、データとして扱います。`spec/pricing.yaml` には更新日、リージョン、出典、リクエスト料金、arm64/x86_64 の GB 秒料金を記録します。サンプルの正規化済み推定値は次のとおりです。

```text
計算コスト = 課金対象時間（秒）× 設定メモリ（GB）× GB 秒料金
リクエストコスト = リクエストあたりの料金
100 万リクエストあたりの正規化コスト = （計算コスト + リクエストコスト）× 1,000,000
```

アカウント間で実行結果を比較可能にするため、主比較から AWS Free Tier を除外します。Free Tier を反映した表示には、その旨を個別に明記する必要があります。料金が未設定の場合は値を補完せず、計算を失敗させます。レポートを解釈する前に、出典と更新日を確認してください。

## ビルド方法

前提条件は、Buildx 対応の Docker、uv、Python 3.13、stable Rust、Terraform、および AWS 操作に使用する AWS CLI です。

```bash
make build              # 両方の linux/arm64 コンテナイメージ
make build-python
make build-rust
make terraform-validate
```

必要に応じて `PLATFORM` または `IMAGE_TAG` を上書きします。制御されたベンチマークでは、`PLATFORM=linux/arm64` を維持してください。

## ローカルでの実行方法

最初に高速なローカルチェックを実行します。

```bash
make lint
make test
```

共通の Contract Test は、URL未指定または `.env.local` の標準ローカル値なら、DynamoDB・Python API・Rust Lambda RIEを起動し、`spec/testcases/` の正本ケースを比較します。既定値で使う場合、環境変数の設定や事前起動は不要です。

```bash
make contract-test
```

コマンドが自動起動したサービスはテスト終了時に停止します。AWSや手動起動済みAPIなど、標準ローカル値とは異なる接続先を使う場合は `PYTHON_API_URL` と `RUST_API_URL` を指定してください。この場合、起動・fixture投入・停止を行わず、指定先へテスト要求だけを送ります。RustのRIEを指定先にする場合は、`RUST_CONTRACT_TRANSPORT=lambda-rie` も設定してください。

このリポジトリでコンテナを用いるローカル実行では、DynamoDB Local を起動し、正本フィクスチャを読み込み、Python ASGI アプリを実行してから、Lambda Runtime Interface Emulator 経由で Rust の Lambda イメージを起動します。

```bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_REGION=us-east-1
docker run --rm -p 8000:8000 amazon/dynamodb-local:latest
aws dynamodb create-table \
  --endpoint-url http://127.0.0.1:8000 \
  --region us-east-1 \
  --table-name lambda-runtime-lab-contract \
  --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
uv run --project tests/contract python tests/contract/seed_fixtures.py \
  --table lambda-runtime-lab-contract \
  --endpoint-url http://127.0.0.1:8000

AWS_ENDPOINT_URL_DYNAMODB=http://127.0.0.1:8000 \
DYNAMODB_TABLE_NAME=lambda-runtime-lab-contract \
uv run --project apps/python --with uvicorn \
  uvicorn app.main:app --app-dir apps/python/src --port 9001

docker run --rm -p 9002:8080 \
  --env AWS_ACCESS_KEY_ID=test \
  --env AWS_SECRET_ACCESS_KEY=test \
  --env AWS_REGION=us-east-1 \
  --env AWS_ENDPOINT_URL_DYNAMODB=http://host.docker.internal:8000 \
  --env DYNAMODB_TABLE_NAME=lambda-runtime-lab-contract \
  lambda-runtime-lab-rust:local

# ローカルサービスを手動で起動済みの場合も、そのまま実行できる
make contract-test
```

Linux では、Rust の `docker run` コマンドに `--add-host host.docker.internal:host-gateway` を追加してください。DynamoDB Local では、AWS SDK の認証情報にダミー値を使用できます。`/items` または `/aggregate` を使用するケースでは、両アプリケーションが同じテーブルを参照している必要があります。`direct` モードが既定です。`lambda-rie` はローカル転送用アダプターにすぎず、正本の HTTP リクエストと期待レスポンスは変更しません。

## デプロイ方法

初回作成、イメージ更新、Lambda切替、確認、切戻し、削除は、[AWSデプロイ手順](docs/06_AWSデプロイ手順.md) を参照してください。API URL やテーブル名の環境変数は、同手順の「共通の準備」どおり `source ./.env.export.sh` で読み込みます。`make publish-images` はECRへのpushだけであり、Lambdaを新digestへ更新するには、出力されたURIを指定して `plan` を確認後に `make deploy` が必要です。

実験後は環境を削除します。

```bash
make destroy
```

## シード方法

`terraform output` からデプロイ済みテーブル名を取得し、`.env` へ書いたうえで決定的なシードスクリプトを実行します。

```bash
source ./.env.export.sh
make seed
```

DynamoDB Local では、`source ./.env.export.sh --local`（`DYNAMODB_ENDPOINT_URL` 含む）を使ってください。コマンドを再実行しても、約 100 件の項目 ID、値、ペイロードは同一になります。

## ベンチマーク方法

保護された両方の API エンドポイント、実際のLambdaアーキテクチャ、メモリを `.env` に設定し、`source ./.env.export.sh` で読み込みます。Python/RustのLambda設定が同じであることをAWS CLIで確認してから、`ARCHITECTURE` と `LAMBDA_MEMORY_MB` を記録してください。ランナーは `X-Benchmark-Run-Id` とリクエスト番号を追加し、ウォームアップを実行し、両対象に同一のワークロード設定を使用し、SigV4 で AWS リクエストに署名し、後から再分析できるよう生の観測値を保存します。

```bash
source ./.env.export.sh
WORKLOAD=aggregate-concurrent \
RUN_ID=auto \
make benchmark
```

同一メモリのマトリクスを明示的に実行します。

```bash
source ./.env.export.sh
make benchmark-matrix
```

この操作はデプロイ済み Lambda の設定を変更し、AWS 料金が発生する可能性があります。

## レポート生成方法

Lambda Duration、Init Duration、メモリはログレコードから取得するため、レポート生成前に実行結果を収集してください。いずれの手順も、その実行の生レコードのみを読み書きします。

```bash
RUN_ID=<existing-run-id> make collect
RUN_ID=<existing-run-id> make report
```

出力先は `results/reports/<run-id>.md` と `results/reports/<run-id>.csv` です。レポートには、条件、サンプル数、パーセンタイル、リソース観測値、イメージメタデータ、正規化コスト、測定時の注記を含めます。

レポートは、最初に`Unclassified=0`と実験条件を確認してから数値を比較します。各章・指標・
`null`の意味、CSVの利用方法、結論を書く際の注意点は
[ベンチマークレポート利用ガイド](docs/07_BENCHMARK_REPORT_GUIDE.md) を参照してください。

## テスト方針

- Python プロジェクトで、Python の単体、API、リポジトリ、エラーテストを実行します。
- Cargo で、Rust の単体、API、リポジトリ、エラーテストを実行します。
- 両言語のテストスイートは、`spec/testcases/` の全ベクターをそれぞれのアプリケーションに対して実行します。そのため、デプロイせずに契約の乖離を高速なローカルテストで検出できます。
- ベンチマークプロジェクトで、ベンチマークランナー、分析器、コスト、レポートの挙動を検証します。
- Contract Test ハーネスの単体テストで、正本ケースの読み込み、厳密な JSON 比較、順序、数値型、非決定的フィールドの明示的な除外を検証します。
- 共通の Contract Test で、各正本ベクターを両 API に送信し、正本のステータス、ボディ、エラースキーマ、順序、数値結果、チェックサムを検証します。
- コンテナのスモーク／契約テストでは、arm64 イメージと共通の決定的なデータソースを使用します。
- Terraform は、リソースをデプロイせずにフォーマット、バックエンドなしの初期化、検証を実行します。

`make test` は暗黙に AWS へ接続しません。`make contract-test` は既定のローカル環境だけを自動起動し、AWSへ接続しません。

## CI

`.github/workflows/ci.yml` はプルリクエストとプッシュ時に実行されます。Python、Rust、共通 Contract Test、`linux/arm64` Docker ビルド、Terraform を個別のジョブで検証します。Contract Test はローカルの Lambda コンテナを使用し、AWS にはデプロイしません。

料金が発生する可能性があるため、完全な AWS デプロイとベンチマークは通常の CI から意図的に除外しています。`.github/workflows/benchmark.yml` は手動の `workflow_dispatch` トリガーのみを持ち、明示的に設定した AWS ロールとイメージ URI を必要とし、保存済みアクセスキーではなく GitHub OIDC を使用します。

## 結果

**未測定です。**

再現可能な実行によって生データとレポートが生成されるまで、ベンチマーク値や言語の優劣は提示しません。

**AWS では未検証です。**

結果を公開する前に、所有する AWS アカウントでデプロイ、IAM 認可、SigV4 呼び出し、CloudWatch との相関、制御されたコールドスタート収集、ECR メタデータ、コスト推定、クリーンアップを検証する必要があります。

## 解釈

結果は単一の順位に集約せず、ワークロードごとに議論してください。

- フレームワークのオーバーヘッド: `/health` はアプリケーション処理と分けて解釈します。
- CPU バウンド処理: 決定的な `/compute` の結果と課金対象時間を比較します。
- I/O バウンド処理: ネットワークと DynamoDB のレイテンシーにより言語差が小さくなる可能性があります。同一の操作回数で逐次・並行プロファイルを比較します。
- コールドスタート: Init Duration と成果物／ランタイムの初期化を分けて比較します。
- リソース使用量: メモリとデプロイ使用量を実行コストから独立して確認します。
- 開発の複雑さ: ランタイムメトリクスとともに、ビルド、依存関係、可観測性、保守性のトレードオフを含めます。

技術選定は、あらかじめ決めた結論ではなく、測定したワークロードと運用上の制約に従うべきです。

## 制約事項

- Lambda のコールドスタートを完全に同一にすることはできません。Init Duration は観測したコールドサンプルを識別しますが、制御された実行環境の更新は分離を改善するにとどまり、保証はしません。
- ウォーム結果には文書化されたウォームアップが必要であり、それでも共有 AWS システム、ネットワークのばらつき、サービスのスロットリング、実行環境の置き換えによる影響を受ける可能性があります。
- API Gateway、DynamoDB、CloudWatch の配信、クライアントの所在地は、言語ランタイム外のレイテンシーに寄与します。
- MVP は arm64 コンテナイメージ、3 種類のメモリ設定、1 つの AWS リージョンを比較するものであり、すべてのアーキテクチャ、リージョン、デプロイ形式、ワークロードに関する一般的な結論ではありません。
- 共通 OS ベースにより変動要因の 1 つは減らせますが、言語ランタイムと依存関係レイヤーは必然的に異なります。
- コスト出力は、設定済みの料金と測定した課金対象時間に基づく推定値であり、AWS 請求書ではありません。

## 今後の作業

- 同等のマトリクスで x86_64 と arm64 を比較する。
- コンテナイメージと ZIP のデプロイモデルを比較する。
- 制御された共通ベースの実行に加え、本番志向のコンテナ比較を追加する。
- 両言語が同じ論理契約を実装した後にのみ、CPU 並列プロファイルを追加する。
- 文書化した信頼区間を伴う、リージョンと時間帯をまたぐ反復実行を拡充する。
- Cloud Run など別のコンピューティングプラットフォームで、同じアプリケーション契約を比較する。

<!--
AWS_PROFILE=lambda-runtime-lab-deploy \
terraform -chdir=infrastructure/terraform output
 -->
