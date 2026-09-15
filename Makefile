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
WORKLOAD ?= aggregate-concurrent
RUN_ID ?= auto
PUBLISH_TAG ?=
PYTHON_API_URL ?=
RUST_API_URL ?=
DYNAMODB_TABLE_NAME ?=
DYNAMODB_ENDPOINT_URL ?=
CONTRACT_TRANSPORT ?= direct
PYTHON_CONTRACT_TRANSPORT ?= $(CONTRACT_TRANSPORT)
RUST_CONTRACT_TRANSPORT ?= $(CONTRACT_TRANSPORT)
TF_AUTO_APPROVE ?=

TF_IMAGE_ARGS := $(if $(strip $(PYTHON_IMAGE_URI)),-var=python_image_uri=$(PYTHON_IMAGE_URI)) \
	$(if $(strip $(RUST_IMAGE_URI)),-var=rust_image_uri=$(RUST_IMAGE_URI))

.PHONY: help format format-check lint test test-python test-rust test-benchmark \
	test-contract-harness contract-test build build-python build-rust \
	terraform-init terraform-validate bootstrap-ecr publish-images deploy seed benchmark collect report \
	benchmark-matrix destroy

help:
	@printf '%s\n' \
		'Quality: format format-check lint test contract-test' \
		'Build:   build build-python build-rust terraform-validate' \
		'AWS:     bootstrap-ecr publish-images deploy seed benchmark collect report benchmark-matrix destroy'

format:
	uv run --project $(PYTHON_DIR) ruff format $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt
	terraform -chdir=$(TERRAFORM_DIR) fmt -recursive

format-check:
	uv run --project $(PYTHON_DIR) ruff format --check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff format --check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff format --check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo fmt --check
	terraform -chdir=$(TERRAFORM_DIR) fmt -check -recursive

lint: format-check
	uv run --project $(PYTHON_DIR) ruff check $(PYTHON_DIR)
	uv run --project $(BENCHMARK_DIR) --extra dev ruff check $(BENCHMARK_DIR)
	uv run --project $(CONTRACT_DIR) ruff check $(CONTRACT_DIR)
	cd $(RUST_DIR) && cargo clippy --all-targets -- -D warnings

test: test-python test-rust test-benchmark test-contract-harness

test-python:
	uv run --project $(PYTHON_DIR) pytest $(PYTHON_DIR)/tests

test-rust:
	cd $(RUST_DIR) && cargo test

test-benchmark:
	uv run --project $(BENCHMARK_DIR) --extra dev pytest $(BENCHMARK_DIR)/tests

test-contract-harness:
	uv run --project $(CONTRACT_DIR) pytest $(CONTRACT_DIR) -m "not integration"

contract-test:
	@test -n "$(PYTHON_API_URL)" || (echo "PYTHON_API_URL is required" >&2; exit 2)
	@test -n "$(RUST_API_URL)" || (echo "RUST_API_URL is required" >&2; exit 2)
	PYTHON_API_URL="$(PYTHON_API_URL)" \
	RUST_API_URL="$(RUST_API_URL)" \
	PYTHON_CONTRACT_TRANSPORT="$(PYTHON_CONTRACT_TRANSPORT)" \
	RUST_CONTRACT_TRANSPORT="$(RUST_CONTRACT_TRANSPORT)" \
	uv run --project $(CONTRACT_DIR) pytest $(CONTRACT_DIR)/test_contract.py -m integration

# Both images are built through the script so that they always share one pinned
# OS-only base digest.
build:
	PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

build-python:
	IMPLEMENTATIONS=python PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

build-rust:
	IMPLEMENTATIONS=rust PLATFORM=$(PLATFORM) TAG=$(IMAGE_TAG) bash scripts/build-images.sh

terraform-init:
	terraform -chdir=$(TERRAFORM_DIR) init -backend=false

terraform-validate:
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
	@test -n "$(PYTHON_IMAGE_URI)" || (echo "PYTHON_IMAGE_URI is required" >&2; exit 2)
	@test -n "$(RUST_IMAGE_URI)" || (echo "RUST_IMAGE_URI is required" >&2; exit 2)
	terraform -chdir=$(TERRAFORM_DIR) init
	terraform -chdir=$(TERRAFORM_DIR) apply $(TF_AUTO_APPROVE) \
		-var=aws_region=$(AWS_REGION) \
		-var=environment=$(ENVIRONMENT) \
		-var=python_image_uri=$(PYTHON_IMAGE_URI) \
		-var=rust_image_uri=$(RUST_IMAGE_URI)

seed:
	@test -n "$(DYNAMODB_TABLE_NAME)" || (echo "DYNAMODB_TABLE_NAME is required" >&2; exit 2)
	$(if $(strip $(DYNAMODB_ENDPOINT_URL)),AWS_ENDPOINT_URL_DYNAMODB="$(DYNAMODB_ENDPOINT_URL)") \
	AWS_REGION="$(AWS_REGION)" \
	AWS_DEFAULT_REGION="$(AWS_REGION)" \
	DYNAMODB_TABLE_NAME="$(DYNAMODB_TABLE_NAME)" \
	uv run --project $(BENCHMARK_DIR) python scripts/seed-data.py \
		--table "$(DYNAMODB_TABLE_NAME)" \
		--region "$(AWS_REGION)"

benchmark:
	@test -n "$(PYTHON_API_URL)" || (echo "PYTHON_API_URL is required" >&2; exit 2)
	@test -n "$(RUST_API_URL)" || (echo "RUST_API_URL is required" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	PYTHON_API_URL="$(PYTHON_API_URL)" \
	RUST_API_URL="$(RUST_API_URL)" \
	PYTHON_API_ENDPOINT="$(PYTHON_API_URL)" \
	RUST_API_ENDPOINT="$(RUST_API_URL)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner run \
		--target both \
		--workload "$(WORKLOAD)" \
		--run-id "$(RUN_ID)"

# Lambda Duration, Init Duration, and memory come from the log records, so a run
# has to be collected before its report can include those metrics.
collect:
	@test "$(RUN_ID)" != "auto" || (echo "RUN_ID must identify an existing run" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner collect \
		--run-id "$(RUN_ID)" \
		--region "$(AWS_REGION)" \
		--python-log-group "/aws/lambda/$$(terraform -chdir=$(TERRAFORM_DIR) output -raw python_lambda_name)" \
		--rust-log-group "/aws/lambda/$$(terraform -chdir=$(TERRAFORM_DIR) output -raw rust_lambda_name)"

report:
	@test "$(RUN_ID)" != "auto" || (echo "RUN_ID must identify an existing run" >&2; exit 2)
	AWS_REGION="$(AWS_REGION)" \
	uv run --project $(BENCHMARK_DIR) python -m benchmark_runner report --run-id "$(RUN_ID)"

benchmark-matrix:
	AWS_REGION="$(AWS_REGION)" \
	bash scripts/benchmark-matrix.sh

destroy:
	terraform -chdir=$(TERRAFORM_DIR) init
	terraform -chdir=$(TERRAFORM_DIR) destroy $(TF_AUTO_APPROVE) \
		-var=aws_region=$(AWS_REGION) \
		-var=environment=$(ENVIRONMENT) \
		$(TF_IMAGE_ARGS)
