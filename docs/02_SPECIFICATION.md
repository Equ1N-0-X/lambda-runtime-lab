# Python vs Rust AWS Lambda Lambdalith Benchmark - 仕様書

## 1. 目的

本書は要件書に基づき、Python LambdalithとRust Lambdalith、Benchmark Runner、Analyzer、Terraform、Docker、テスト、CIの具体仕様を定義する。

実装上の最優先事項は以下とする。

1. **API Contractは共通化する。**
2. **内部実装は各言語で自然な方式を採用する。**
3. **比較条件をコード・設定として再現可能にする。**
4. **ベンチマーク結果を捏造しない。**
5. **MVPに不要な抽象化・マイクロサービス化を行わない。**

---

## 2. 推奨リポジトリ構成

```text
lambda-lambdalith-benchmark/
├── README.md
├── Makefile
├── .gitignore
├── .editorconfig
│
├── spec/
│   ├── openapi.yaml
│   ├── workloads.yaml
│   └── pricing.yaml
│
├── apps/
│   ├── python/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── uv.lock                 # uv採用時
│   │   ├── src/
│   │   │   └── app/
│   │   │       ├── main.py
│   │   │       ├── api/
│   │   │       ├── domain/
│   │   │       ├── services/
│   │   │       ├── repositories/
│   │   │       ├── infrastructure/
│   │   │       └── config.py
│   │   └── tests/
│   │
│   └── rust/
│       ├── Dockerfile
│       ├── Cargo.toml
│       ├── Cargo.lock
│       ├── src/
│       │   ├── main.rs
│       │   ├── api/
│       │   ├── domain/
│       │   ├── services/
│       │   ├── repositories/
│       │   ├── infrastructure/
│       │   └── config.rs
│       └── tests/
│
├── benchmark/
│   ├── pyproject.toml
│   ├── src/
│   │   └── benchmark_runner/
│   │       ├── cli.py
│   │       ├── runner.py
│   │       ├── workloads.py
│   │       ├── cloudwatch.py
│   │       ├── analyzer.py
│   │       ├── cost.py
│   │       └── report.py
│   └── tests/
│
├── infrastructure/
│   └── terraform/
│       ├── versions.tf
│       ├── providers.tf
│       ├── variables.tf
│       ├── locals.tf
│       ├── ecr.tf
│       ├── lambda.tf
│       ├── apigateway.tf
│       ├── dynamodb.tf
│       ├── iam.tf
│       ├── logs.tf
│       ├── outputs.tf
│       └── terraform.tfvars.example
│
├── scripts/
│   ├── build-images.sh
│   ├── push-images.sh
│   ├── seed-data.py
│   ├── benchmark-matrix.sh
│   └── clean-results.sh
│
├── results/
│   ├── raw/
│   │   └── .gitkeep
│   └── reports/
│       └── .gitkeep
│
└── .github/
    └── workflows/
        ├── ci.yml
        └── benchmark.yml
```

構成は責務分離を目的とし、将来使うかもしれないという理由だけの階層追加は行わない。

---

## 3. 技術スタック

### 3.1 Python Lambdalith

- Python: バージョンを明示的に固定
- FastAPI
- Mangum
- Pydantic
- AWS SDK
- pytest
- ruff
- uvまたは同等のlock可能なパッケージ管理

### 3.2 Rust Lambdalith

- Rust stable toolchainを固定
- Edition 2024を基本候補とする
- Axum
- lambda_http
- Tokio
- Serde
- AWS SDK for Rust
- Rayon: CPU Parallel benchmarkを実装する場合のみ
- cargo fmt
- clippy

### 3.3 Benchmark Runner

- Python
- argparse / Typer等の軽量CLI
- boto3
- HTTP client
- botocore等を利用したSigV4署名
- pandasは必須としない。標準csv + statistics等で十分ならそちらを優先する。

### 3.4 Infrastructure

- AWS Lambda
- Amazon ECR
- Amazon API Gateway HTTP API
- Amazon DynamoDB
- Amazon CloudWatch Logs
- IAM
- Terraform

---

## 4. コンテナ仕様

### 4.1 共通原則

Python版・Rust版とも最終runtime stageのベースを同一のLambda向けAL2023 OS-only imageにし、比較runでは可能な限り同一digestへ固定する。

```text
Final Runtime Base
  └─ AL2023 OS-only Lambda base
       ├─ Python image: CPython + RIC + dependencies + app
       └─ Rust image: native binary + required runtime components
```

ビルド用stageは言語ごとに異なってよい。

### 4.2 Python Dockerfile

Multi-stage buildとする。

要件:

- build stageでPythonランタイム・依存関係を再現可能に準備する。
- final stageは共通AL2023 OS-only base。
- Lambda Runtime Interface Clientを含める。
- FastAPIアプリをMangumでLambda eventへ適合させる。
- 不要なbuild tool、cache、test dependencyをfinal imageへ含めない。
- dependency lockを利用する。

### 4.3 Rust Dockerfile

Multi-stage buildとする。

要件:

- builder stageでrelease build。
- `linux/arm64` をMVP標準targetとする。
- final stageはPython版と同一AL2023 OS-only base。
- final stageにコンパイラ・Cargo registry等を含めない。
- バイナリstrip等のサイズ最適化を検討する。ただし性能へ影響する最適化はREADMEに明記する。

### 4.4 Image metadata

イメージにはOCI label等で最低限以下を記録できるようにする。

- implementation: python / rust
- git revision
- build timestamp
- target architecture

---

## 5. API共通仕様

`spec/openapi.yaml` を **唯一の正規API Contract** とする。

Python版・Rust版ともこれを満たす。

### 5.1 共通HTTP仕様

- Content-Type: `application/json`
- UTF-8
- JSON key naming: `snake_case`
- Success responseは実装言語に依存しない。
- Error responseも両言語で同一schema。
- 実装名はresponse bodyに含めない。
- Benchmark run IDはHTTP headerで受け取る。

推奨header:

```text
X-Benchmark-Run-Id: <uuid>
```

---

## 6. エンドポイント仕様

## 6.1 GET /health

### 目的

Router、Framework、Lambda runtime等の最小オーバーヘッドを測る。

### Request

なし。

### Response 200

```json
{
  "status": "ok"
}
```

### 外部I/O

なし。

---

## 6.2 POST /transform

### 目的

JSON decode、validation、domain transform、JSON encodeの比較。

### Request

```json
{
  "records": [
    {
      "id": "item-001",
      "name": "  Example Name  ",
      "tags": ["b", "a", "b"],
      "value": 123
    }
  ]
}
```

### 処理仕様

各recordに対して以下を行う。

1. `id` を必須検証する。
2. `name` をtrimする。
3. `name` をlowercaseへ正規化する。
4. `tags` の重複を除去する。
5. `tags` を昇順sortする。
6. `value` を整数として検証する。
7. `records` は `id` 昇順で返す。

### Response 200

```json
{
  "records": [
    {
      "id": "item-001",
      "name": "example name",
      "tags": ["a", "b"],
      "value": 123
    }
  ],
  "count": 1
}
```

### 外部I/O

なし。

### 制約

- records上限はworkload設定で定義する。
- 両実装で同じvalidation条件にする。

---

## 6.3 POST /compute

### 目的

CPU Bound処理を比較する。

### Request

```json
{
  "items": 100,
  "iterations": 1000,
  "seed": "benchmark-seed",
  "profile": "sequential"
}
```

MVPで有効な`profile`:

- `sequential`

将来拡張として `parallel` を追加できる。ただし **Python/Rustの両方で同一の論理仕様を実装し、Contract Testを満たした時点でのみCanonical API Contractへ追加する**。片方だけが`parallel`を成功させる状態は作らない。

### 論理処理

1. seedとindexから決定的なbyte列を生成する。
2. 各itemについてSHA-256等の決定的計算を`iterations`回行う。
3. itemごとのdigestを昇順index順で連結する。
4. 最終digestを算出する。
5. 実行順序に依存しない同一結果を返す。

### Response 200

```json
{
  "processed": 100,
  "iterations": 1000,
  "checksum": "<hex digest>"
}
```

### 重要事項

将来Parallel profileを追加する場合、内部の処理順は異なってよいが、Sequentialと同じ入力から同じ最終結果を返す。

---

## 6.4 GET /items/{id}

### 目的

単一DynamoDB I/Oとserializationの比較。

### DynamoDB Item

```json
{
  "id": "item-001",
  "value": 123,
  "payload": "deterministic payload"
}
```

### Response 200

```json
{
  "item": {
    "id": "item-001",
    "value": 123,
    "payload": "deterministic payload"
  }
}
```

### Response 404

共通Error schemaを返す。

---

## 6.5 POST /aggregate

### 目的

複数DynamoDB I/OのSequential / Concurrent比較。

### Request

```json
{
  "ids": ["item-001", "item-002", "item-003"],
  "profile": "concurrent"
}
```

`profile`:

- `sequential`
- `concurrent`

### 処理

1. idsごとに同じDynamoDB GetItem相当処理を行う。
2. `sequential`では直列実行する。
3. `concurrent`では各言語の自然なasync方式で並行実行する。
4. 結果をid順に正規化する。
5. count、value合計、payloadのchecksumを計算する。

### Response 200

```json
{
  "count": 3,
  "total_value": 369,
  "checksum": "<hex digest>"
}
```

外部I/O回数はPython/Rustで必ず一致させる。

---

## 7. 共通Error schema

FastAPIの標準validation errorとRust側独自errorをそのまま返さず、両実装で次の形式に統一する。

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "request validation failed",
    "details": [
      {
        "field": "records[0].id",
        "reason": "required"
      }
    ]
  }
}
```

主なerror code:

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `UNSUPPORTED_PROFILE`
- `INTERNAL_ERROR`
- `DEPENDENCY_ERROR`

Internal errorにstack traceや内部情報を返さない。

---

## 8. Application設計

## 8.1 共通責務

論理責務は概ね以下とする。

```text
API / Handler
    ↓
Application Service
    ↓
Domain Logic
    ↓
Repository
    ↓
AWS SDK
```

ただし全処理をinterface化しない。

Repository境界など、外部I/Oを分離する価値がある場所に限定して抽象化する。

## 8.2 Python

例:

```text
main.py
  ├─ FastAPI app
  └─ Mangum handler

api/
  ├─ health.py
  ├─ transform.py
  ├─ compute.py
  ├─ items.py
  └─ aggregate.py

services/
repositories/
infrastructure/
```

DynamoDB client等はmodule initialization時に生成し、Warm Startで再利用する。

## 8.3 Rust

例:

```text
main.rs
  ├─ AppState
  ├─ Axum Router
  └─ Lambda runtime start

api/
services/
repositories/
infrastructure/
```

DynamoDB client等はAppStateに保持し、Lambda execution environment内で再利用する。

---

## 9. Logging仕様

アプリケーションログはstructured JSONとする。

最低限のfield:

```json
{
  "level": "INFO",
  "message": "request completed",
  "implementation": "python",
  "lambda_request_id": "...",
  "benchmark_run_id": "...",
  "route": "/aggregate",
  "profile": "concurrent",
  "status_code": 200
}
```

Python/Rustとも同じfield名を使用する。

Secret、AWS credential、request payload全文等を無条件にログへ出さない。

---

## 10. DynamoDB仕様

### 10.1 Table

Table nameはTerraformで生成する。

Partition Key:

```text
id: String
```

属性:

- `id`
- `value`
- `payload`

### 10.2 Seed

`scripts/seed-data.py` で決定的な100件程度のデータを生成する。

例:

```text
item-001 ... item-100
```

同じseedから常に同じvalue/payloadを生成する。

---

## 11. Benchmark workload仕様

`spec/workloads.yaml` にワークロードを定義する。

例:

```yaml
version: 1
workloads:
  health:
    method: GET
    path: /health
    warmup: 20
    iterations: 200
    client_concurrency: 1

  transform-small:
    method: POST
    path: /transform
    warmup: 20
    iterations: 200
    client_concurrency: 4
    payload_file: benchmark/payloads/transform-small.json

  compute-medium:
    method: POST
    path: /compute
    warmup: 10
    iterations: 100
    client_concurrency: 1
    body:
      items: 100
      iterations: 1000
      seed: benchmark-seed
      profile: sequential

  aggregate-concurrent:
    method: POST
    path: /aggregate
    warmup: 20
    iterations: 200
    client_concurrency: 4
    body:
      ids:
        - item-001
        - item-002
        - item-003
        - item-004
        - item-005
      profile: concurrent
```

実際の値はローカル・AWS実行時間が過大にならないよう調整する。

---

## 12. Benchmark Runner仕様

CLI例:

```bash
python -m benchmark_runner run \
  --target all \
  --workload aggregate-concurrent \
  --run-id auto \
  --terraform-dir infrastructure/terraform
```

### 12.1 Runner責務

1. workload読込
2. target endpoint取得
3. Benchmark run ID生成
4. warm-up
5. request送信
6. client-side latency記録
7. status/error記録
8. raw結果保存
9. CloudWatch収集処理の起動または後続実行

### 12.2 Raw record例

```json
{
  "run_id": "...",
  "implementation": "python",
  "workload": "aggregate-concurrent",
  "request_index": 1,
  "started_at": "...",
  "latency_ms": 24.1,
  "status_code": 200,
  "success": true
}
```

---

## 13. CloudWatch収集仕様

CloudWatchログから最低限以下を取得する。

- Lambda RequestId
- Duration
- Billed Duration
- Init Duration
- Max Memory Used
- Memory Size

アプリstructured logの `lambda_request_id` と `benchmark_run_id` を利用してBenchmark sampleとLambda REPORT情報を関連付ける。

Coldサンプルは `Init Duration` が取得できた実行等から分類する。

取得不能なfieldを0として捏造せず、nullとして扱う。

---

## 14. Image計測仕様

### 14.1 Local

Docker build後に以下を保存する。

- image ID
- total size
- layer history
- build duration

### 14.2 ECR

push後に以下を保存する。

- repository
- image digest
- image size bytes
- pushed timestamp

### 14.3 Artifact-specific

Rust:

- final binary size

Python:

- CPython/runtime directory size
- site-packages size
- application source size

測定方法をスクリプト化し、READMEに定義する。

---

## 15. Cost calculator仕様

`spec/pricing.yaml` 例:

```yaml
updated_at: YYYY-MM-DD
region: ap-northeast-1
source: "AWS Lambda pricing page"
request_price_per_million: 0.0
compute_price_per_gb_second:
  arm64: 0.0
  x86_64: 0.0
```

値は実装時に最新の公式情報で更新する。

### 15.1 計算

最低限以下を計算する。

```text
compute_cost = billed_duration_seconds × configured_memory_gb × gb_second_price
request_cost = request_price_per_request
normalized_cost_per_1m = (compute_cost + request_cost) × 1,000,000
```

各サンプル平均だけでなく、P50/P95等から参考値を出してもよい。

無料枠を差し引く計算は別表示とする。

---

## 16. Report仕様

生成先:

```text
results/reports/<run_id>.md
results/reports/<run_id>.csv
```

Markdown reportには最低限以下を含める。

1. 実験条件
2. Git revision
3. AWS region
4. Architecture
5. Lambda memory
6. Image digest
7. Workload
8. Python集計
9. Rust集計
10. 差分率
11. Cost comparison
12. Cold / Warm distinction
13. Image size comparison
14. 注意事項

結論は自動生成で断定しすぎず、数値を提示する。

READMEでは複数runを基に人間が考察を記載できるようにする。

---

## 17. Terraform仕様

### 17.1 Variables

最低限:

- `aws_region`
- `project_name`
- `lambda_memory_mb`
- `lambda_timeout_seconds`
- `architecture`
- `image_uris`（実装キーからイメージURIへの対応表。`make publish-images` が `images.auto.tfvars.json` へ書き出す）
- `environment`

比較対象の言語は `spec/implementations.yaml` を正本とし、Terraformは `yamldecode` で読み込む。

### 17.2 Lambda

全言語で次を同一にする。

- Memory
- Timeout
- Architecture
- Environment variables
- Logging retention
- IAM permissions

差はimage URI等、実装に必要な部分に限定する。

### 17.3 API Gateway

全言語を同条件で比較できるよう、同等構成のHTTP APIを用意する。

各APIはLambdalithへproxy integrationする。

公開状態のまま放置しない。IAM認証等の保護を利用し、Benchmark RunnerはAWS credential chainを利用してSigV4署名したrequestを送信する。

### 17.4 Outputs

言語を追加しても出力名が変わらないよう、言語ごとの値は実装キーの対応表で出力する。

- api_endpoints
- lambda_names
- ecr_repository_urls
- dynamodb_table_name

---

## 18. Benchmark Matrix

MVP標準:

```text
Architecture: arm64
Memory:       256 / 512 / 1024 MB
Implementations:
  - Python
  - Rust
Workloads:
  - health
  - transform
  - compute sequential
  - items
  - aggregate sequential
  - aggregate concurrent
```

`benchmark-matrix.sh` はmemoryごとにTerraform applyし、両Lambdaを同じMemoryへ変更して計測する。

Architecture比較はMVP後とする。

---

## 19. Contract Test仕様

Canonical test vectorsを共有する。

例:

```text
spec/testcases/
├── health.json
├── transform-valid.json
├── transform-invalid.json
├── compute.json
├── item-found.json
├── item-not-found.json
└── aggregate.json
```

Contract testは両実装へ同じrequestを与え、非決定値を除いて厳密比較する。

最低限:

- response status
- response JSON
- error code
- ordering
- checksum

---

## 20. CI仕様

### ci.yml

Pull Request / Pushで実行:

```text
Python
  - install
  - ruff
  - pytest

Rust
  - cargo fmt --check
  - cargo clippy -- -D warnings
  - cargo test

Contract
  - local/container contract tests

Docker
  - build Python image
  - build Rust image

Terraform
  - terraform fmt -check
  - terraform init -backend=false
  - terraform validate
```

### benchmark.yml

Manual triggerのみ。

AWSへのdeployやbenchmarkはSecret/Role設定がある場合だけ実行する。

---

## 21. Makefile仕様

最低限:

```text
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

コマンドは可能な限り冪等にする。

---

## 22. README仕様

READMEは転職ポートフォリオとして、コードを読まなくても意図が分かる構成にする。

推奨章立て:

1. Project Overview
2. Why this project exists
3. What is being compared
4. Comparison principles: same WHAT, idiomatic HOW
5. Architecture
6. Endpoints / workloads
7. Controlled container comparison
8. Metrics
9. How to run locally
10. How to deploy
11. How to run benchmark
12. How cost is calculated
13. Results
14. Interpretation / trade-offs
15. Limitations
16. Future work

重要:

- 「Rustの優位性を証明する」等の結論ありきで書かない。
- I/O Boundでは差が小さくなる可能性も正しく扱う。
- Image sizeとLambda実行料金を混同しない。
- Cold Start測定には環境依存性があることを明記する。
- 実測していない数値を記載しない。

---

## 23. 実装判断ルール

複数案がある場合は以下を優先する。

1. 同一API Contractを守る。
2. 実験条件を揃える。
3. 各言語で自然な実装を選ぶ。
4. 単純な構成を優先する。
5. 外部依存を増やしすぎない。
6. ベンチマークに不要な機能を実装しない。
7. 将来拡張だけを理由にinterface/moduleを増やさない。
8. コストの発生する処理は明示的な操作でのみ実行する。

---

## 24. 実装完了条件

以下がすべて成立した時点でMVP完了とする。

- Python/Rust両Lambdalithが同じAPI Contractを満たす。
- 5 endpointが動作する。
- DynamoDB seed dataでitems/aggregateが動作する。
- 両Containerが同一AL2023 final baseでbuildできる。
- arm64 buildができる。
- Terraform validateが通る。
- AWS環境へ2 Lambdaをdeployできる。
- Benchmark Runnerが両方を同条件で呼べる。
- CloudWatch metrics/log dataを収集できる。
- Image sizeを収集できる。
- Markdown/CSV reportを生成できる。
- Cost calculatorが設定ファイルから計算できる。
- Unit/Contract testが通る。
- CIが通る。
- `make destroy` でAWSリソースを安全に削除できる。
- READMEに測定方法・制約・技術選定観点が説明されている。
