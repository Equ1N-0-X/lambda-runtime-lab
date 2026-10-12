# 開発・品質確認・AWS比較実行を、同じ引数と順序で呼び出す共通の入口を定義する。
SHELL := /usr/bin/env bash

PYTHON_DIR ?= apps/python
RUST_DIR ?= apps/rust
BENCHMARK_DIR ?= benchmark
CONTRACT_DIR ?= tests/contract
TERRAFORM_DIR ?= infrastructure/terraform

PLATFORM ?= linux/arm64
IMAGE_TAG ?= local
PYTHON_IMAGE ?= lambda-runtime-lab-python:$(IMAGE_TAG)
RUST_IMAGE ?= lambda-runtime-lab-rust:$(IMAGE_TAG)
PYTHON_IMAGE_URI ?=
RUST_IMAGE_URI ?=

AWS_REGION ?= ap-northeast-1
ENVIRONMENT ?= benchmark
ARCHITECTURE ?=
LAMBDA_MEMORY_MB ?=
WORKLOAD ?= aggregate-concurrent
RUN_ID ?= auto
PUBLISH_TAG ?=
PYTHON_API_URL ?=
RUST_API_URL ?=
DYNAMODB_TABLE_NAME ?=
DYNAMODB_ENDPOINT_URL ?=
CONTRACT_TRANSPORT ?= direct
PYTHON_CONTRACT_TRANSPORT ?=
RUST_CONTRACT_TRANSPORT ?=
TF_AUTO_APPROVE ?=
UI_PORT ?= 8765

TF_IMAGE_ARGS := $(if $(strip $(PYTHON_IMAGE_URI)),-var=python_image_uri=$(PYTHON_IMAGE_URI)) \
	$(if $(strip $(RUST_IMAGE_URI)),-var=rust_image_uri=$(RUST_IMAGE_URI))

.PHONY: help format format-check lint test test-python test-rust test-benchmark \
	test-contract-harness contract-test build build-python build-rust \
	terraform-init terraform-validate bootstrap-ecr publish-images deploy seed benchmark collect report \
	benchmark-matrix destroy ui

help:
	# 利用可能な操作を品質確認、ビルド、AWS操作の順に短く表示する。
	@printf '%s\n' \
		'Quality: format format-check lint test contract-test' \
		'Build:   build build-python build-rust terraform-validate' \
		'AWS:     bootstrap-ecr publish-images deploy seed benchmark collect report benchmark-matrix destroy' \
		'View:    ui'

format:
	# Python、ベンチマーク、契約テスト、Rust、Terraformをそれぞれの標準フォーマッタで整形する。
	uv run --project $(PYTHON_DIR) ruff format $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt
	terraform -chdir=$(TERRAFORM_DIR) fmt -recursive

format-check:
	# ファイルを書き換えず、コミット前に全言語の書式違反を検出する。
	uv run --project $(PYTHON_DIR) ruff format --check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format --check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format --check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt --check
	terraform -chdir=$(TERRAFORM_DIR) fmt -check -recursive

lint: format-check
	# 書式確認に通ったソースだけを静的解析し、警告も失敗として扱う。
	uv run --project $(PYTHON_DIR) ruff check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo clippy --all-targets -- -D warnings

test: test-python test-rust test-benchmark test-contract-harness
	# 実装別・計測ツール別・契約ハーネス別のテストをまとめて実行する。

test-python:
	# 仮想環境移動後のpytestランチャーshebangに依存せず、環境内Pythonからテストモジュールを起動する。
	uv run --project $(PYTHON_DIR) python -m pytest $(PYTHON_DIR)/tests

test-rust:
	cd $(RUST_DIR) && cargo test

test-benchmark:
	# benchmarkの開発依存を読み込み、同じPython環境からテストモジュールを起動する。
	uv run --project $(BENCHMARK_DIR) --extra dev python -m pytest $(BENCHMARK_DIR)/tests

test-contract-harness:
	# AWS実環境を必要とするintegration以外の契約ハーネステストを実行する。
	uv run --project $(CONTRACT_DIR) python -m pytest $(CONTRACT_DIR) -m "not integration"

contract-test:
	# URL未指定ならローカル依存サービスを自動準備し、URL指定時は既存の外部接続先だけを検証する。
	PYTHON_API_URL="$(PYTHON_API_URL)" \
	RUST_API_URL="$(RUST_API_URL)" \
	DYNAMODB_TABLE_NAME="$(DYNAMODB_TABLE_NAME)" \
	DYNAMODB_ENDPOINT_URL="$(DYNAMODB_ENDPOINT_URL)" \
	RUST_IMAGE="$(RUST_IMAGE)" \
	AWS_REGION="$(AWS_REGION)" \
	CONTRACT_TRANSPORT="$(CONTRACT_TRANSPORT)" \
	PYTHON_CONTRACT_TRANSPORT="$(PYTHON_CONTRACT_TRANSPORT)" \
	RUST_CONTRACT_TRANSPORT="$(RUST_CONTRACT_TRANSPORT)" \
	bash scripts/run-contract-test.sh

# 両イメージを同じスクリプトで作り、固定したOS専用ベースdigestを必ず共有させる。
build:
	# 同じ固定ベースイメージでPython/Rustの両コンテナを作成し、比較条件を揃える。
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
	# 両実装の不変イメージURIが指定された場合だけ、LambdaなどのAWSリソースを適用する。
	@test -n "$(PYTHON_IMAGE_URI)" || (echo "PYTHON_IMAGE_URI の指定が必要です" >&2; exit 2)
	@test -n "$(RUST_IMAGE_URI)" || (echo "RUST_IMAGE_URI の指定が必要です" >&2; exit 2)
	terraform -chdir=$(TERRAFORM_DIR) init
	terraform -chdir=$(TERRAFORM_DIR) apply $(TF_AUTO_APPROVE) \
		-var=aws_region=$(AWS_REGION) \
		-var=environment=$(ENVIRONMENT) \
		-var=python_image_uri=$(PYTHON_IMAGE_URI) \
		-var=rust_image_uri=$(RUST_IMAGE_URI)

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
	# 接続先と実際のLambda条件が指定された場合だけ、同じワークロードを両実装へ送信して結果を保存する。
	@test -n "$(PYTHON_API_URL)" || (echo "PYTHON_API_URL の指定が必要です" >&2; exit 2)
	@test -n "$(RUST_API_URL)" || (echo "RUST_API_URL の指定が必要です" >&2; exit 2)
	@test -n "$(ARCHITECTURE)" || (echo "ARCHITECTURE の指定が必要です" >&2; exit 2)
	@test -n "$(LAMBDA_MEMORY_MB)" || (echo "LAMBDA_MEMORY_MB の指定が必要です" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	PYTHON_API_URL="$(PYTHON_API_URL)" \
	RUST_API_URL="$(RUST_API_URL)" \
	PYTHON_API_ENDPOINT="$(PYTHON_API_URL)" \
	RUST_API_ENDPOINT="$(RUST_API_URL)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner run \
		--target both \
		--workload "$(WORKLOAD)" \
		--architecture "$(ARCHITECTURE)" \
		--memory-mb "$(LAMBDA_MEMORY_MB)" \
		--run-id "$(RUN_ID)"

# Lambdaの実行時間、初期化時間、メモリ量はログから取得するため、レポート作成前に収集を行う。
collect:
	# autoではない既存の実行IDを指定した場合だけ、CloudWatchログを結果へ結合する。
	@test "$(RUN_ID)" != "auto" || (echo "RUN_ID には既存の実行IDを指定してください" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner collect \
		--run-id "$(RUN_ID)" \
		--region "$(AWS_REGION)" \
		--python-log-group "/aws/lambda/$$(terraform -chdir=$(TERRAFORM_DIR) output -raw python_lambda_name)" \
		--rust-log-group "/aws/lambda/$$(terraform -chdir=$(TERRAFORM_DIR) output -raw rust_lambda_name)"

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
		-var=environment=$(ENVIRONMENT) \
		$(TF_IMAGE_ARGS)
