# Python vs Rust AWS Lambda Lambdalith Benchmark - 要件書

## 1. 文書の目的

本書は、転職・技術ポートフォリオとして公開する **Python版とRust版のAWS Lambda Lambdalith比較プロジェクト** の要件を定義する。

本プロジェクトは単純な言語処理速度の比較ではなく、**同一の業務仕様をPythonとRustでそれぞれ自然かつ効率的に実装し、AWS Lambda上で性能・メモリ・デプロイ成果物・クラウドコスト・実装特性を実測して技術選定の判断材料を示すこと** を目的とする。

---

## 2. プロジェクトの狙い

公開リポジトリを通じて、以下の能力を一体として示す。

- PythonによるWeb/APIアプリケーション設計・実装
- RustによるWeb/APIアプリケーション設計・実装
- AWS Lambdaを中心としたサーバーレス設計
- AWS API Gateway / DynamoDB / CloudWatch / ECR / IAMの利用
- TerraformによるIaC
- Docker / Multi-stage build / コンテナ最適化
- 同一仕様に対する複数実装の設計
- 同期・非同期・並行・並列処理の使い分け
- Lambda実行環境の再利用を考慮したリソースライフサイクル設計
- 性能計測・負荷試験・統計処理
- 実行時間、メモリ、デプロイサイズ、コストを踏まえた技術選定
- CI/CD、自動テスト、静的解析、再現可能な実験環境の構築
- 技術的トレードオフを定量的に説明する能力

---

## 3. 基本コンセプト

### 3.1 比較対象

以下の2つのLambdalithを作成する。

1. **Python Lambdalith**
2. **Rust Lambdalith**

それぞれ1つのLambda関数の中に4〜5個程度のAPIエンドポイントを持たせる。

### 3.2 Lambdalithの定義

本プロジェクトにおけるLambdalithとは、複数のAPIルートとアプリケーションロジックを **1つのLambda関数にまとめた小規模なWeb/APIアプリケーション** を指す。

巨大なモノリスを作ることは目的としない。比較に必要な処理特性を含む最小構成とする。

### 3.3 比較の原則

比較では、内部実装の手順を完全一致させる必要はない。

統一するのは **WHAT（何をするか）** であり、**HOW（どう実装するか）** は各言語の特性を活かす。

両実装で必ず一致させるものは以下とする。

- API仕様
- 入力データ
- 出力データ
- 外部I/O回数および意味
- エラー条件
- 計算対象
- 集約ルール
- タイムアウト・再試行等の実験条件
- ベンチマーク条件

一方、以下は言語ごとに自然な実装を許可する。

- 非同期ランタイム
- 並行処理方式
- 並列処理方式
- リソース初期化方式
- SDKクライアントの保持方式
- JSONシリアライズ/デシリアライズ
- バリデーション実装
- 内部モジュール構成

この方針により、研究用途の純粋な言語ベンチマークではなく、**実際にそれぞれの言語を採用した場合のアプリケーションスタック比較** とする。

---

## 4. 対象システム

### 4.1 全体像

```text
                         Benchmark Runner
                                |
                    +-----------+-----------+
                    |                       |
              Python API                Rust API
                    |                       |
             API Gateway               API Gateway
                    |                       |
          Python Lambdalith          Rust Lambdalith
                    |                       |
                    +-----------+-----------+
                                |
                            DynamoDB
                                |
                           CloudWatch

Container Images -> ECR
Infrastructure   -> Terraform
Reports          -> Markdown / CSV / JSON
```

### 4.2 共通インフラ

原則としてPython版とRust版で以下の条件を共通化する。

- AWSリージョン
- CPUアーキテクチャ
- Lambdaメモリ設定
- Lambdaタイムアウト
- API Gateway構成
- DynamoDBテーブル
- データセット
- IAM権限ポリシー
- ログ設定
- ベンチマーク実行条件
- コンテナの最終ベースOS

---

## 5. コンテナ要件

### 5.1 基本方針

初期バージョンでは、Python版・Rust版とも **同一のAWS Lambda向けAL2023 OS-onlyベースイメージ** を最終ランタイムイメージとして利用する。

目的は、OS層を可能な限り共通化し、以下の差を観察しやすくすることである。

- 言語ランタイム
- 依存ライブラリ
- アプリケーションコード
- ネイティブバイナリ
- イメージサイズ
- 初期化コスト
- メモリ使用量

### 5.2 Pythonコンテナ

Python版は最終AL2023ベース上に以下を含める。

- CPython
- AWS Lambda Runtime Interface Client
- Pythonアプリケーション
- FastAPI
- Mangum
- Pydantic
- AWS SDK等の必要最小限の依存関係

CPythonを最終ベースに導入する方法は、再現性の高いMulti-stage buildとし、最終イメージのOSベースをRust版と揃える。

### 5.3 Rustコンテナ

Rust版はMulti-stage buildを用い、最終AL2023ベースには原則として以下のみを配置する。

- コンパイル済みアプリケーションバイナリ
- Lambda実行に必要な最小構成

主要ライブラリ候補は以下とする。

- Axum
- lambda_http
- Tokio
- Serde
- AWS SDK for Rust

### 5.4 イメージサイズ比較

以下を計測対象とする。

- ローカルDockerイメージ総サイズ
- ECR上のイメージサイズ
- レイヤー構成
- アプリケーション/ランタイム差分層のサイズ
- Rustバイナリサイズ
- Python依存ライブラリおよびランタイム層のサイズ

イメージサイズは直接Lambda実行料金と同義ではないため、**デプロイ成果物のフットプリント指標** として扱う。

---

## 6. API要件

Lambdalithは、処理特性の異なる5エンドポイントを持つ。

| エンドポイント | 主目的 | 主な特性 |
|---|---|---|
| `GET /health` | 最小オーバーヘッド確認 | Router / Framework / Runtime |
| `POST /transform` | JSON変換・検証 | Validation / Serialization |
| `POST /compute` | CPU負荷 | CPU Bound / 並列化候補 |
| `GET /items/{id}` | 単一データ取得 | DynamoDB I/O |
| `POST /aggregate` | 複数データ取得と集約 | Async / Concurrent I/O |

APIの詳細仕様は仕様書で定義する。

---

## 7. 並行・並列・同期・非同期処理要件

### 7.1 基本方針

処理方式を無理に揃えず、各言語で自然な実装とする。

ただし、同じAPI仕様・同じ計算量・同じ外部I/O条件を満たすこと。

### 7.2 I/O Bound

`POST /aggregate` では、複数のDynamoDB読み取りを対象として以下を比較できる構成とする。

- Sequential
- Concurrent

Pythonの候補:

- asyncio
- 非同期対応AWSクライアントまたは適切な非同期ラッパー

Rustの候補:

- Tokio
- AWS SDK for Rustのasync API

### 7.3 CPU Bound

`POST /compute` は独立した複数の計算単位を処理し、以下を比較可能とする。

- Sequential
- Parallel

RustではRayon等、Pythonではmultiprocessing / ProcessPool等を候補とする。

ただしCPU ParallelismはLambdaのvCPU割当、プロセス起動コスト、IPC、GIL等の影響を含むため、**初期MVPでは共通APIとして有効化しない**。拡張時はPython/Rustの両方で同一の論理仕様を満たすParallel profileを実装し、Contract Testを通したうえで比較対象へ追加する。

PythonのThreadPool等も、CPU処理内容とGILの影響を踏まえて採用可否を判断する。

### 7.4 リソース再利用

Lambda実行環境のWarm reuseを活かすため、以下の重いリソースはhandler呼び出しごとに再生成しない。

- DynamoDBクライアント
- HTTPクライアントを導入する場合のHTTPクライアント
- 設定値
- ルータ/アプリケーション状態

Pythonではmodule global等、RustではApplication State / OnceLock等を利用できる。

本プロジェクトではこれを「Singletonパターン比較」とは呼ばず、**Resource Lifecycle / Execution Environment Reuse** として扱う。

---

## 8. ベンチマーク要件

### 8.1 計測対象

#### Build / Artifact

- Build time
- Container image size
- ECR image size
- Layer size
- Rust binary size
- Python runtime/dependency size

#### Runtime

- Client-side end-to-end latency
- Lambda Duration
- Billed Duration
- Init Duration
- Max Memory Used
- Error count / Error rate
- P50
- P95
- P99

#### Cost

- 1リクエストあたり推定コスト
- 1,000,000リクエスト換算コスト
- ワークロード単位のコスト
- 性能あたりコスト

### 8.2 Cold / Warm

Cold StartとWarm Startを分けて扱う。

- Warm計測前には十分なwarm-upを行う。
- Cold StartはInit Duration等を利用してColdサンプルを識別する。
- 必要に応じてLambda設定変更等で実行環境を更新し、Controlled Cold Startモードを実装する。
- Cold Startは完全に同一条件を保証できない場合があるため、計測方法をREADMEに明記する。

### 8.3 ベンチマークデータ

- 元データはJSON LinesまたはJSONで保存する。
- 集計結果はCSVとMarkdownで出力する。
- 生データを削除せず、再集計可能にする。
- 架空のベンチマーク結果をリポジトリに記載しない。

### 8.4 料金計算

料金ロジックをコードに固定値で散在させない。

料金設定は設定ファイルに分離し、最低限以下を保持する。

- 更新日
- AWS料金ページ等の参照元
- Request pricing
- GB-second pricing
- Architecture差がある場合の価格

無料枠を含む結果と、無料枠を除外した正規化結果を混同しない。

ポートフォリオ上の主比較は、**無料枠を除外した正規化コスト** とする。

---

## 9. Benchmark Runner要件

外部から2つのLambdalithへ同一条件のリクエストを送り、計測・収集する **Benchmark Runner** を作成する。

RunnerはPythonで実装してよい。

最低限以下を指定可能にする。

- 対象実装: Python / Rust / Both
- Endpoint
- Iterations
- Client concurrency
- Warm-up回数
- Payload
- Benchmark run ID
- Sequential / Concurrent等のProfile

Runnerは各リクエストにBenchmark run IDを付与し、CloudWatchログと突合できるようにする。

---

## 10. DynamoDB要件

Python版とRust版で共通のDynamoDBテーブルを利用する。

- 同一データを読み取る。
- Benchmark用データは決定的に生成する。
- Seedスクリプトを用意する。
- Terraformで業務データそのものを大量投入しない。
- ベンチマーク終了後に安全に削除可能な構成とする。

---

## 11. Terraform要件

Terraformで最低限以下を管理する。

- ECR repository x 2
- Lambda function x 2
- API Gateway x 2、または同条件となる等価構成
- Lambda/API Gateway integration
- DynamoDB table
- IAM role / policy
- CloudWatch log configuration
- 必要なoutputs

Python版とRust版の設定値は可能な限り同じTerraform定義から生成する。

不要なmodule分割は行わない。共通化に意味がある範囲だけ抽象化する。

---

## 12. テスト要件

### 12.1 Unit Test

各言語で以下をテストする。

- ドメインロジック
- 変換処理
- CPU計算処理
- 集約処理
- エラーハンドリング
- Cost calculator

### 12.2 Contract Test

最重要テストとして、Python版とRust版に同じ入力を与え、以下を比較する。

- HTTP Status
- Response body
- Error schema
- 並び順
- 数値結果

実装言語固有の値をレスポンス本文に含めず、比較不能な差を作らない。

### 12.3 Integration Test

- DynamoDB Localまたは実AWS環境でのRepositoryテスト
- Lambda/API Gateway integration smoke test
- Container startup test

### 12.4 Benchmark Smoke Test

少数回の実行でRunner・ログ収集・集計処理が動作することを確認する。

---

## 13. CI/CD要件

GitHub Actions等で以下を自動化する。

- Python lint / format / test
- Rust fmt / clippy / test
- Contract test
- Container build
- Terraform fmt / validate

AWSに料金が発生する本格ベンチマークは通常CIでは自動実行しない。

必要な場合はManual workflow dispatch等で明示的に実行する。

---

## 14. セキュリティ要件

- AWS認証情報をリポジトリに保存しない。
- Terraform stateにSecretを極力含めない。
- APIを常時無認証公開しない。
- ベンチマーク用APIはIAM認証等で保護し、Benchmark RunnerからSigV4署名等でアクセスする。
- IAMは最小権限を原則とする。
- ログに認証情報・Secretを出力しない。
- DynamoDBはBenchmark用途に限定する。
- Benchmark環境は容易にdestroyできるようにする。

---

## 15. 非機能要件

### 15.1 再現性

以下を固定・記録する。

- Python version
- Rust toolchain
- Cargo.lock
- Python dependency lock file
- Docker base image tag / digest
- Python/Rustで共通となるfinal base image digest
- Target architecture
- Terraform version constraint
- AWS region
- Benchmark条件

### 15.2 可読性

- 必要以上の抽象化をしない。
- 同じ理由で変更される処理をまとめる。
- 言語ごとに自然な設計を優先する。
- 「比較のためだけの不自然な共通化」を行わない。

### 15.3 運用性

最低限以下のコマンドを提供する。

```bash
make build
make test
make deploy
make seed
make benchmark
make report
make destroy
```

---

## 16. MVP範囲

初期公開版では以下を必須とする。

- Python Lambdalith 1関数
- Rust Lambdalith 1関数
- 5エンドポイント
- 同一API Contract
- 同一AL2023最終ベースコンテナ
- arm64を標準ターゲット
- Terraform
- ECR
- API Gateway
- DynamoDB
- CloudWatch
- Benchmark Runner
- Warm / Cold分類
- Duration / Memory / Image Size / Cost集計
- Markdown / CSVレポート
- Unit / Contract / Integration smoke test
- GitHub Actions
- README

---

## 17. 初期MVPのスコープ外

以下は初期版では必須としない。

- Kubernetes
- Step Functions
- SQS / SNS
- RDS
- 外部SaaS API
- 複雑な認証基盤
- 高度な分散トレーシング
- Python multiprocessingの本格比較
- 多数のLambda分割
- 高負荷商用レベルのLoad Test

---

## 18. 将来拡張候補

### 18.1 Architecture比較

- arm64
- x86_64

### 18.2 デプロイ方式比較

- Container Image
- ZIP package

Rust ZIP版はOS-only runtime向けネイティブバイナリ、Python ZIP版はmanaged runtimeを利用する構成を候補とする。

### 18.3 Production-oriented Container比較

Controlled comparisonとは別に、各言語で一般的な本番向けベースイメージを利用した比較を追加できる。

### 18.4 CPU Parallelism

- Python multiprocessing / ProcessPool
- Python ThreadPoolの適用可否
- Rust Rayon
- Lambda memory/vCPUとの関係

### 18.5 Cloud Run等との比較

将来的に同一アプリケーションをCloud Run等へ展開し、Compute platform選定まで拡張できる。

---

## 19. 成果物

リポジトリには最低限以下を含める。

- Python実装
- Rust実装
- OpenAPI仕様
- Benchmark workload仕様
- Terraform
- Dockerfiles
- Benchmark Runner
- Analyzer / Cost calculator
- Seed script
- Test suite
- CI workflow
- Makefile
- README
- 生データ保存領域
- 生成レポート保存領域

---

## 20. 成功条件

本プロジェクトは、単に「Rustの方が速い」等の結論を出すことを成功条件としない。

以下を満たせば成功とする。

1. 同一仕様のLambdalithをPythonとRustで実装できている。
2. 各言語で不自然な実装を強制していない。
3. 同一条件で再現可能なベンチマークを実行できる。
4. 実行時間、Cold Start、メモリ、イメージサイズ、コストを比較できる。
5. CPU Bound / I/O Bound / Framework overhead等で差がどのように変わるか説明できる。
6. 数値結果から「どの条件ならPython/Rustを選ぶべきか」を説明できる。
7. コード、Terraform、テスト、CI、READMEを含め、第三者が構成と意図を理解できる。

---

## 21. 用語統一

本プロジェクトでは以下の表記に統一する。

| 用語 | 本書での意味 |
|---|---|
| Python Lambdalith | Pythonで実装した1 Lambda構成のAPIアプリケーション |
| Rust Lambdalith | Rustで実装した1 Lambda構成のAPIアプリケーション |
| Lambdalith | 複数APIルートを1 Lambdaに集約した小規模アプリケーション |
| Benchmark Runner | API呼び出し、負荷生成、Run ID付与を行う外部計測クライアント |
| Analyzer | 生データとCloudWatch情報を集計する処理 |
| Benchmark Profile | Sequential / Concurrent / Parallel等の実行方式 |
| Cold Start | Lambda実行環境初期化を伴う実行 |
| Warm Start | 既存Lambda実行環境を再利用する実行 |
| Resource Lifecycle | SDKクライアント等の初期化・保持・再利用方法 |
| Controlled comparison | OS等の比較条件を可能な限り揃えた比較 |
| Idiomatic implementation | 各言語で自然かつ実用的な実装 |
