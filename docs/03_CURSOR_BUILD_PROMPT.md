# Cursor実装用プロンプト

以下の要件に従って、転職用に公開可能なGitHubリポジトリを **このワークスペース内に一式実装してください**。

作業途中で設計を必要以上に巨大化しないでください。MVPを最後まで完成させることを優先します。

参照文書:

- `01_REQUIREMENTS.md`
- `02_SPECIFICATION.md`

この2文書を正として実装してください。

---

## 1. プロジェクトの目的

同一仕様のAWS Lambda LambdalithをPythonとRustでそれぞれ1つずつ実装し、次を実測・比較できる公開ポートフォリオを作成してください。

- 実行時間
- Cold Start / Warm Start
- Billed Duration
- Max Memory Used
- Client-side latency
- P50 / P95 / P99
- Container image size
- ECR image size
- Runtime / dependency / binary footprint
- Build time
- 1 request / 1,000,000 requests換算のLambda推定コスト

単なる「Python vs Rustのマイクロベンチマーク」にせず、**実際の小規模APIアプリケーションを2言語で実装した場合の技術選定比較** にしてください。

---

## 2. 最重要設計原則

### 同じWHAT、異なるHOW

Python版とRust版で以下は必ず一致させてください。

- API Contract
- 入力
- 出力
- エラー条件
- 計算内容
- DynamoDBアクセス回数と意味
- Benchmark条件

内部実装は各言語で自然かつ効率的な方式を採用してください。

例えば:

- Python I/O concurrency: asyncio系
- Rust I/O concurrency: Tokio
- Python resource reuse: module global等
- Rust resource reuse: AppState / OnceLock等
- Rust CPU parallelism: Rayon候補

内部処理順まで無理に一致させる必要はありません。

ただし最終結果は決定的で、Contract Testにより一致を確認できるようにしてください。

---

## 3. アプリケーション

以下の2アプリを作ってください。

```text
Python Lambdalith: 1 Lambda
Rust Lambdalith:   1 Lambda
```

各Lambdalithは次の5 endpointを持ちます。

```text
GET  /health
POST /transform
POST /compute
GET  /items/{id}
POST /aggregate
```

API詳細は `02_SPECIFICATION.md` に従ってください。

Canonical API Contractとして `spec/openapi.yaml` を作成し、両実装をそれに合わせてください。

---

## 4. Python実装

基本構成:

- FastAPI
- Mangum
- Pydantic
- AWS SDK
- pytest
- ruff
- dependency lock

責務を以下程度に分離してください。

```text
API
Application Service
Domain Logic
Repository
Infrastructure
```

ただし、全クラスにinterfaceを作るような過剰設計は禁止です。

DynamoDB client等はLambda handlerごとに生成せず、execution environment内で再利用してください。

FastAPI標準validation errorをそのまま返さず、仕様書の共通Error schemaへ変換してください。

---

## 5. Rust実装

基本構成:

- Axum
- lambda_http
- Tokio
- Serde
- AWS SDK for Rust
- cargo fmt
- clippy

DynamoDB client等はAppState等へ保持し、execution environment内で再利用してください。

`/aggregate` のconcurrent profileではTokioを自然に使用してください。

`/compute` はMVPでは共通の `sequential` profileのみを有効にしてください。将来 `parallel` を追加する場合は、Python/Rustの両方で同一の論理仕様を実装し、Contract Testを満たしてからCanonical API Contractへ追加してください。Rustだけ先行してAPI上の `parallel` を成功させる実装にはしないでください。

Rustでも共通Error schemaを厳密に守ってください。

---

## 6. Container

Python版・Rust版とも **最終runtime imageは同一のAWS Lambda向けAL2023 OS-only base** を使用し、比較時は可能な限り同一digestへ固定してください。

Multi-stage buildにしてください。

### Python

final imageへ最低限以下を含めてください。

- CPython
- Lambda Runtime Interface Client
- application
- runtime dependencies

Python runtimeの導入方法は、再現性と保守性を優先してください。

もしAL2023 final baseへCPythonを直接package installする方式が安定しない場合は、builder stageでPython runtimeを準備してfinal AL2023へコピーする方式を採用してください。

### Rust

builder stageでrelease buildし、final AL2023へ必要なnative binaryのみをコピーしてください。

MVP標準architecture:

```text
linux/arm64
```

Apple Silicon Macからもbuild可能なようDocker Buildx等を考慮してください。

---

## 7. DynamoDB

共通DynamoDB tableを1つ作成してください。

Partition Key:

```text
id: String
```

Item:

```text
id
value
payload
```

`scripts/seed-data.py` を作成し、同一seedから100件程度の決定的データを投入できるようにしてください。

Terraformで大量のbenchmark dataを直接管理しないでください。

---

## 8. Terraform

`infrastructure/terraform` に実装してください。

最低限作成するもの:

- ECR repository x2
- Lambda x2
- API Gateway HTTP API x2または同等条件の構成
- API Gateway -> Lambda integration
- DynamoDB x1
- IAM role / policy
- CloudWatch log関連
- Outputs

Python/Rust Lambdaで次は同一条件にしてください。

- Memory
- Timeout
- Architecture
- IAM
- Logging
- Environment variables

Terraform変数として最低限次を持たせてください。

```text
aws_region
project_name
lambda_memory_mb
lambda_timeout_seconds
architecture
python_image_uri
rust_image_uri
environment
```

APIは常時無認証公開しないでください。IAM認証等、Benchmark Runnerから利用可能な安全な方式を採用してください。Benchmark RunnerはAWS credential chainを使い、botocore等でSigV4署名してAPIへアクセスしてください。

不要なTerraform module分割は禁止です。

---

## 9. Benchmark Runner

PythonでCLIを作ってください。

配置:

```text
benchmark/
```

主なコマンドイメージ:

```bash
python -m benchmark_runner run --target both --workload aggregate-concurrent
python -m benchmark_runner collect --run-id <id>
python -m benchmark_runner report --run-id <id>
```

Runnerの責務:

1. `spec/workloads.yaml` を読む
2. Python/Rust APIへ同一requestを送る
3. Benchmark run IDを生成する
4. `X-Benchmark-Run-Id` を付ける
5. warm-upする
6. client-side latencyを測る
7. status/errorを保存する
8. raw JSON/JSONLを保存する
9. CloudWatchデータを収集する
10. Analyzerへ渡す

async/concurrent request generationは必要に応じて使って構いません。

---

## 10. CloudWatch metrics/log収集

CloudWatchから可能な範囲で次を取得してください。

- Lambda RequestId
- Duration
- Billed Duration
- Init Duration
- Max Memory Used
- Memory Size

アプリ側structured logに最低限次を含めてください。

```text
implementation
lambda_request_id
benchmark_run_id
route
profile
status_code
```

RunnerのsampleとLambda REPORTをRequestId / Benchmark run IDで関連付けてください。

取得できない値を0で埋めず、nullとして扱ってください。

Cold StartはInit Duration等から分類してください。

---

## 11. Image size計測

自動計測スクリプトを作ってください。

Python/Rustそれぞれについて以下を取得してください。

- Docker image total size
- Docker layer history
- Docker build duration
- ECR image size
- image digest

追加で:

Python:

- Python runtime size
- site-packages size
- app source size

Rust:

- release binary size

結果はraw benchmark metadataへ含めてください。

Image sizeはLambda実行料金そのものではないため、READMEでその点を明記してください。

---

## 12. Cost calculator

`spec/pricing.yaml` と `benchmark/.../cost.py` を作成してください。

料金をソースコードへ直接ハードコードしないでください。

pricing configには次を持たせてください。

```text
updated_at
region
source
request_price_per_million
compute_price_per_gb_second.arm64
compute_price_per_gb_second.x86_64
```

実装時にAWS公式料金を確認できる場合は最新値を設定してください。

確認できない場合は **架空の料金値を入れず**、明示的なTODO/placeholderとして、料金値未設定ではcost calculationを実行しないよう安全に失敗させてください。

計算:

```text
compute_cost
= billed_duration_seconds
× configured_memory_gb
× gb_second_price

request_cost
= request_price_per_request
```

1,000,000 request換算を出してください。

Free Tierあり/なしを混同しないでください。

---

## 13. Report

次を生成してください。

```text
results/raw/<run_id>/...
results/reports/<run_id>.md
results/reports/<run_id>.csv
```

Markdown reportには最低限次を含めてください。

- Run ID
- Git revision
- Date/time
- AWS region
- Architecture
- Lambda memory
- Workload
- Iterations
- Python/Rust image digest
- Image sizes
- Build time
- Cold/Warm sample count
- Duration
- Billed Duration
- Init Duration
- Max Memory Used
- Client latency
- P50/P95/P99
- Error rate
- Estimated cost / request
- Estimated cost / 1M requests
- Python/Rust差分率
- Measurement notes

架空データやサンプル数値を正式な実測結果として出力しないでください。

---

## 14. Benchmark Profile

`spec/workloads.yaml` で定義してください。

MVP:

```text
health
transform-small
transform-large
compute-sequential
items
aggregate-sequential
aggregate-concurrent
```

Memory matrix:

```text
256 MB
512 MB
1024 MB
```

MVP標準architecture:

```text
arm64
```

`benchmark-matrix.sh` でMemoryを順番にTerraform applyし、Python/Rustを同一memory条件にしてbenchmarkできるようにしてください。

x86_64比較はFuture Workにしてください。

---

## 15. Test

必須:

### Python

- Unit Test
- API Test
- Repository Test

### Rust

- Unit Test
- API Test
- Repository Test

### Shared Contract Test

同一requestをPython/Rustへ送り、以下を比較してください。

- status code
- response body
- error schema
- ordering
- checksum

Canonical testcaseを `spec/testcases/` に置いてください。

Contract Testはこのプロジェクトの重要な価値なので、READMEでも説明してください。

---

## 16. CI

`.github/workflows/ci.yml` を作成してください。

Push / Pull Requestで最低限:

```text
Python
  lint
  test

Rust
  fmt check
  clippy -D warnings
  test

Docker
  Python build
  Rust build

Terraform
  fmt check
  init -backend=false
  validate
```

AWSへdeployして料金が発生する本格benchmarkは通常CIでは実行しないでください。

Manual workflowとして `.github/workflows/benchmark.yml` を作っても構いません。

---

## 17. Makefile

最低限次を提供してください。

```bash
make format
make lint
make test
make build
make build-python
make build-rust
make terraform-validate
make deploy
make seed
make benchmark
make report
make benchmark-matrix
make destroy
```

READMEに実行順序を記載してください。

---

## 18. README

公開ポートフォリオとして重要です。

コードだけ作って終わらず、READMEを完成させてください。

必須章:

```text
Overview
Motivation
What is a Lambdalith in this repository
Comparison policy
Architecture
Technology stack
API endpoints
Benchmark workloads
Container strategy
Metrics
Cost calculation
How to build
How to deploy
How to seed
How to benchmark
How to generate reports
Testing strategy
CI
Results
Interpretation
Limitations
Future work
```

READMEの主張は中立的にしてください。

禁止例:

```text
Rust is always better than Python.
```

望ましい方向:

```text
The project evaluates where the runtime and implementation differences matter enough to justify a technology choice.
```

CPU Bound / I/O Bound / Framework overhead / Cold Start / Resource usage / Development complexityを分けて考察できる構成にしてください。

実測前はResultsに架空値を書かず、`Not measured yet` としてください。

---

## 19. コード品質方針

次を守ってください。

- High Cohesion
- Low Coupling
- Separation of Concerns
- Encapsulation
- Composition over Inheritance
- KISS
- YAGNI

ただしパターン適用自体を目的にしないでください。

特に以下は禁止します。

- 将来使うかもしれないinterface
- Repositoryが1個しかないのに不要なgeneric repository framework
- DI container導入だけを目的とした複雑化
- 過剰なBaseClass
- 設計意図のないFactory乱用
- PythonとRustの内部構造を無理に同型にすること

外部I/O境界やテスト容易性に意味がある範囲だけ抽象化してください。

---

## 20. セキュリティ

- AWS credentialをcommitしない。
- `.env` をcommitしない。
- Secretをlogしない。
- IAM最小権限。
- Benchmark APIを無認証で常時公開しない。
- `make destroy` で環境を削除可能にする。
- Public repositoryにaccount ID等の不要な情報を残さない。

---

## 21. 実装手順

以下の順で進めてください。

### Phase 1: Contract

1. repo skeleton作成
2. `spec/openapi.yaml`
3. common error schema
4. canonical testcases
5. workload spec

### Phase 2: Applications

6. Python Lambdalith
7. Rust Lambdalith
8. local API/unit tests
9. Contract Test

### Phase 3: Container

10. Python Dockerfile
11. Rust Dockerfile
12. arm64 build
13. image size collection

### Phase 4: Infrastructure

14. Terraform
15. ECR
16. Lambda x2
17. API Gateway
18. DynamoDB
19. IAM / Logs
20. seed script

### Phase 5: Benchmark

21. Benchmark Runner
22. CloudWatch collector
23. Analyzer
24. Cost calculator
25. Report generator
26. memory matrix script

### Phase 6: Quality

27. Makefile
28. CI
29. README
30. final validation

途中で過剰な機能追加をしないでください。

---

## 22. 最終検証

実装完了後、必ず自身でリポジトリ全体を再確認してください。

確認項目:

### Contract

- Python/RustのAPI仕様に差がないか
- validation条件に差がないか
- error schemaに差がないか
- checksumが一致するか

### Benchmark fairness

- Lambda memoryが同一か
- architectureが同一か
- timeoutが同一か
- DynamoDB dataが同一か
- 外部I/O回数が同一か
- workload inputが同一か
- warm-up/iteration/concurrencyが同一か

### Container

- final base image tagだけでなくdigestも同一か
- build dependencyがfinal imageに残っていないか
- arm64 buildになっているか

### Terraform

- fmtが通るか
- validateが通るか
- Python/Rust設定値が不用意に異なっていないか
- IAMが過剰権限になっていないか

### Tests

- Python tests
- Rust tests
- Contract tests
- Container build
- Terraform validate

### Documentation

- READMEだけで目的が分かるか
- 「同じWHAT、異なるHOW」が説明されているか
- Image sizeを料金と誤解させる書き方がないか
- Cold Startの制約が説明されているか
- 未実測の数値を記載していないか
- 用語が `01_REQUIREMENTS.md` / `02_SPECIFICATION.md` と一致しているか

### Simplicity

- 不要な抽象化がないか
- 同じ処理が不必要に重複していないか
- 複雑さに理由があるか
- MVP外の機能を作り込みすぎていないか

問題があれば修正してから完了してください。

---

## 23. 完了条件

次をすべて満たした状態を完了とします。

```text
[ ] Python Lambdalith実装済み
[ ] Rust Lambdalith実装済み
[ ] 5 endpoint実装済み
[ ] OpenAPI作成済み
[ ] 共通Error Contract実装済み
[ ] Contract Test成功
[ ] Python Unit Test成功
[ ] Rust Unit Test成功
[ ] Docker build成功
[ ] 同一AL2023 final base確認
[ ] arm64 build確認
[ ] Terraform validate成功
[ ] Benchmark Runner完成
[ ] CloudWatch collector完成
[ ] Image size collector完成
[ ] Cost calculator完成
[ ] Report generator完成
[ ] Makefile完成
[ ] CI完成
[ ] README完成
[ ] 未実測値を捏造していない
[ ] セキュリティチェック済み
[ ] 最終セルフレビュー済み
```

AWS credentialsがない、またはAWSへdeployできない環境の場合でも、そこで作業を停止しないでください。

その場合は最低限以下まで完成させてください。

- source code
- tests
- local/container validation
- Docker build
- Terraform validate
- Benchmark Runnerのdry-run / unit test
- report generator
- README

実AWSでのみ確認できる項目は、READMEの `Not yet verified on AWS` に明示してください。

**不足情報を理由に全体を止めず、合理的なデフォルトを選び、判断をREADMEまたはADRに記録して最後まで実装してください。**
