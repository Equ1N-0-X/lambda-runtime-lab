#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
MEMORIES="${MEMORIES:-256 512 1024}"
WORKLOADS="${WORKLOADS:-health transform-small transform-large compute-sequential items aggregate-sequential aggregate-concurrent}"
ARCHITECTURE="${ARCHITECTURE:-arm64}"
REGION="${AWS_REGION:-ap-northeast-1}"
LOG_WAIT_SECONDS="${LOG_PROPAGATION_WAIT_SECONDS:-10}"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

runner=(env "PYTHONPATH=${ROOT}/benchmark/src" python3 -m benchmark_runner)

execute() {
  if "${DRY_RUN}"; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

for memory in ${MEMORIES}; do
  execute terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
    -var="lambda_memory_mb=${memory}"

  if "${DRY_RUN}"; then
    python_endpoint="<terraform:python_api_endpoint>"
    rust_endpoint="<terraform:rust_api_endpoint>"
    python_log_group="<terraform:python_lambda_name>"
    rust_log_group="<terraform:rust_lambda_name>"
  else
    python_endpoint="$(terraform -chdir="${TERRAFORM_DIR}" output -raw python_api_endpoint)"
    rust_endpoint="$(terraform -chdir="${TERRAFORM_DIR}" output -raw rust_api_endpoint)"
    python_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw python_lambda_name)"
    rust_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw rust_lambda_name)"
    python_log_group="/aws/lambda/${python_name}"
    rust_log_group="/aws/lambda/${rust_name}"
  fi

  for workload in ${WORKLOADS}; do
    run_id="arm64-${memory}-${workload}-$(date -u +%Y%m%dT%H%M%SZ)"
    execute "${runner[@]}" run --target both --workload "${workload}" \
      --run-id "${run_id}" --region "${REGION}" --architecture "${ARCHITECTURE}" \
      --memory-mb "${memory}" --python-endpoint "${python_endpoint}" \
      --rust-endpoint "${rust_endpoint}"
    if ! "${DRY_RUN}"; then
      sleep "${LOG_WAIT_SECONDS}"
    fi
    execute "${runner[@]}" collect --run-id "${run_id}" --region "${REGION}" \
      --python-log-group "${python_log_group}" --rust-log-group "${rust_log_group}"
    execute "${runner[@]}" report --run-id "${run_id}"
  done
done
