# 開発・品質確認・AWS比較実行を、同じ引数と順序で呼び出す共通の入口を定義する。
SHELL := /usr/bin/env bash

PYTHON_DIR ?= apps/python
RUST_DIR ?= apps/rust
TYPESCRIPT_DIR ?= apps/typescript
JAVA_DIR ?= apps/java
BENCHMARK_DIR ?= benchmark
CONTRACT_DIR ?= tests/contract
TERRAFORM_DIR ?= infrastructure/terraform

PLATFORM ?= linux/arm64
IMAGE_TAG ?= local

AWS_REGION ?= ap-northeast-1
ENVIRONMENT ?= benchmark
ARCHITECTURE ?=
LAMBDA_MEMORY_MB ?=
WORKLOAD ?= aggregate-concurrent
RUN_ID ?= auto
# 計測する言語。all か、実装キーのカンマ区切り（例: python,rust）。
TARGET ?= all
PUBLISH_TAG ?=
DYNAMODB_TABLE_NAME ?=
DYNAMODB_ENDPOINT_URL ?=
TF_AUTO_APPROVE ?=
UI_PORT ?= 8765

# TypeScriptは依存が未導入ならlockファイルどおりに導入してからnpmスクリプトを実行する。
TYPESCRIPT_NPM = (test -d $(TYPESCRIPT_DIR)/node_modules || npm --prefix $(TYPESCRIPT_DIR) ci --no-audit --no-fund) && npm --prefix $(TYPESCRIPT_DIR)

# JavaはMavenがあればそれを使い、なければ同じバージョンのMavenコンテナで実行する。
# 正本ケースを読むため、コンテナにはリポジトリ全体を載せる。依存はDockerボリュームに保持する。
JAVA_MVN = $(if $(shell command -v mvn),cd $(JAVA_DIR) && mvn,docker run --rm -v "$(CURDIR)":/repo -v lambda-runtime-lab-m2:/root/.m2 -w /repo/$(JAVA_DIR) maven:3.9-eclipse-temurin-25 mvn)

# 実装ごとのイメージURIは make publish-images が書き出し、Terraformが自動で読み込む。
IMAGES_FILE := $(TERRAFORM_DIR)/images.auto.tfvars.json

.PHONY: help format format-check lint test test-python test-rust test-typescript test-java test-benchmark \
	test-contract-harness contract-test build build-python build-rust \
	terraform-init terraform-validate bootstrap-ecr publish-images deploy seed benchmark collect report \
	benchmark-matrix destroy ui

help:
	# 利用可能な操作を品質確認、ビルド、AWS操作の順に短く表示する。
	@printf '%s\n' \
		'Quality: format format-check lint test contract-test' \
		'Build:   build build-python build-rust terraform-validate（言語を絞る: make build IMPLEMENTATIONS="python rust"）' \
		'AWS:     bootstrap-ecr publish-images deploy seed benchmark collect report benchmark-matrix destroy' \
		'View:    ui'

format:
	# Python、ベンチマーク、契約テスト、Rust、Terraformをそれぞれの標準フォーマッタで整形する。
	uv run --project $(PYTHON_DIR) ruff format $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt
	$(TYPESCRIPT_NPM) run format
	terraform -chdir=$(TERRAFORM_DIR) fmt -recursive

format-check:
	# ファイルを書き換えず、コミット前に全言語の書式違反を検出する。
	uv run --project $(PYTHON_DIR) ruff format --check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format --check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format --check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt --check
	$(TYPESCRIPT_NPM) run format:check
	terraform -chdir=$(TERRAFORM_DIR) fmt -check -recursive

lint: format-check
	# 書式確認に通ったソースだけを静的解析し、警告も失敗として扱う。
	uv run --project $(PYTHON_DIR) ruff check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo clippy --all-targets -- -D warnings
	$(TYPESCRIPT_NPM) run typecheck
	$(JAVA_MVN) -B -q compile

test: test-python test-rust test-typescript test-java test-benchmark test-contract-harness
	# 実装別・計測ツール別・契約ハーネス別のテストをまとめて実行する。

test-python:
	# 仮想環境移動後のpytestランチャーshebangに依存せず、環境内Pythonからテストモジュールを起動する。
	uv run --project $(PYTHON_DIR) python -m pytest $(PYTHON_DIR)/tests

test-rust:
	cd $(RUST_DIR) && cargo test

test-typescript:
	$(TYPESCRIPT_NPM) test

test-java:
	# コンパイルと単体テスト（正本ケースを含む）を実行する。
	$(JAVA_MVN) -B -q test

test-benchmark:
	# benchmarkの開発依存を読み込み、同じPython環境からテストモジュールを起動する。
	uv run --project $(BENCHMARK_DIR) --extra dev python -m pytest $(BENCHMARK_DIR)/tests

test-contract-harness:
	# AWS実環境を必要とするintegration以外の契約ハーネステストを実行する。
	uv run --project $(CONTRACT_DIR) python -m pytest $(CONTRACT_DIR) -m "not integration"

contract-test:
	# <KEY>_API_URLが未指定なら、全言語のイメージをLambda RIEで起動して比較する。
	# 指定時は既存の外部接続先だけを検証する（環境変数はそのまま引き継ぐ）。
	DYNAMODB_TABLE_NAME="$(DYNAMODB_TABLE_NAME)" \
	DYNAMODB_ENDPOINT_URL="$(DYNAMODB_ENDPOINT_URL)" \
	IMAGE_TAG="$(IMAGE_TAG)" \
	AWS_REGION="$(AWS_REGION)" \
	bash scripts/run-contract-test.sh

# 全言語のイメージを同じスクリプトで作り、固定したOS専用ベースdigestを必ず共有させる。
build:
	# 同じ固定ベースイメージで全言語のコンテナを作成し、比較条件を揃える。
	PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

build-python:
	IMPLEMENTATIONS=python PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

build-rust:
	IMPLEMENTATIONS=rust PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

terraform-init:
	terraform -chdir=$(TERRAFORM_DIR) init -backend=false

terraform-validate:
	# AWSへ変更を加えず、Terraformの書式・初期化・構文と参照整合性を確認する。
	terraform -chdir=$(TERRAFORM_DIR) fmt -check -recursive
	terraform -chdir=$(TERRAFORM_DIR) init -backend=false
	terraform -chdir=$(TERRAFORM_DIR) validate

bootstrap-ecr:
	AWS_REGION="$(AWS_REGION)" ENVIRONMENT="$(ENVIRONMENT)" TF_AUTO_APPROVE="$(TF_AUTO_APPROVE)" \
		bash scripts/bootstrap-ecr.sh

publish-images:
	AWS_REGION="$(AWS_REGION)" \
		$(if $(strip $(PUBLISH_TAG)),TAG="$(PUBLISH_TAG)") \
		bash scripts/publish-images.sh

deploy:
	# make publish-images が書き出した全言語の不変イメージURIがある場合だけ、AWSリソースを適用する。
	@test -f "$(IMAGES_FILE)" || (echo "$(IMAGES_FILE) がありません。先に make publish-images を実行してください" >&2; exit 2)
	terraform -chdir=$(TERRAFORM_DIR) init
	terraform -chdir=$(TERRAFORM_DIR) apply $(TF_AUTO_APPROVE) \
		-var=aws_region=$(AWS_REGION) \
		-var=environment=$(ENVIRONMENT)

seed:
	# テーブル名が指定された場合だけ、比較用の決定的な初期データをDynamoDBへ投入する。
	@test -n "$(DYNAMODB_TABLE_NAME)" || (echo "DYNAMODB_TABLE_NAME の指定が必要です" >&2; exit 2)
	$(if $(strip $(DYNAMODB_ENDPOINT_URL)),AWS_ENDPOINT_URL_DYNAMODB="$(DYNAMODB_ENDPOINT_URL)") \
	AWS_REGION="$(AWS_REGION)" \
	AWS_DEFAULT_REGION="$(AWS_REGION)" \
	DYNAMODB_TABLE_NAME="$(DYNAMODB_TABLE_NAME)" \
	uv run --project $(BENCHMARK_DIR) python scripts/seed-data.py \
		--table "$(DYNAMODB_TABLE_NAME)" \
		--region "$(AWS_REGION)"

benchmark:
	# 実際のLambda条件が指定された場合だけ、同じワークロードを全言語へ送信して結果を保存する。
	# 接続先は環境変数 <KEY>_API_URL を優先し、なければTerraformの出力から読む。
	@test -n "$(ARCHITECTURE)" || (echo "ARCHITECTURE の指定が必要です" >&2; exit 2)
	@test -n "$(LAMBDA_MEMORY_MB)" || (echo "LAMBDA_MEMORY_MB の指定が必要です" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner run \
		--target "$(TARGET)" \
		--workload "$(WORKLOAD)" \
		--architecture "$(ARCHITECTURE)" \
		--memory-mb "$(LAMBDA_MEMORY_MB)" \
		--run-id "$(RUN_ID)" \
		--terraform-dir "$(TERRAFORM_DIR)"

# Lambdaの実行時間、初期化時間、メモリ量はログから取得するため、レポート作成前に収集を行う。
collect:
	# autoではない既存の実行IDを指定した場合だけ、CloudWatchログを結果へ結合する。
	@test "$(RUN_ID)" != "auto" || (echo "RUN_ID には既存の実行IDを指定してください" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner collect \
		--run-id "$(RUN_ID)" \
		--region "$(AWS_REGION)" \
		--terraform-dir "$(TERRAFORM_DIR)"

report:
	# autoではない既存の実行IDを指定した場合だけ、保存済み測定値からレポートを作成する。
	@test "$(RUN_ID)" != "auto" || (echo "RUN_ID には既存の実行IDを指定してください" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner report --run-id "$(RUN_ID)"

ui:
	# 計測結果の閲覧と計測開始の画面を、手元のマシンだけで起動する。
	# 接続先と比較条件は、画面がTerraformの出力とAWS上のLambdaの実設定から求める。
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner ui \
		--port "$(UI_PORT)" \
		--terraform-dir "$(TERRAFORM_DIR)"

benchmark-matrix:
	AWS_REGION="$(AWS_REGION)" \
	bash scripts/benchmark-matrix.sh

destroy:
	# Terraformで作成したAWSリソースを削除する。実行前に対象環境とTF_AUTO_APPROVEを確認する。
	terraform -chdir=$(TERRAFORM_DIR) init
	terraform -chdir=$(TERRAFORM_DIR) destroy $(TF_AUTO_APPROVE) \
		-var=aws_region=$(AWS_REGION) \
		-var=environment=$(ENVIRONMENT)
