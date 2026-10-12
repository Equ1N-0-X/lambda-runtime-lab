#!/usr/bin/env bash
# メモリ量とワークロードの全組み合わせを、Python/Rustへ同一条件で実行・収集・報告する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
MEMORIES="${MEMORIES:-256 512 1024}"
WORKLOADS="${WORKLOADS:-health transform-small transform-large compute-sequential items aggregate-sequential aggregate-concurrent}"
ARCHITECTURE="${ARCHITECTURE:-arm64}"
REGION="${AWS_REGION:-ap-northeast-1}"
LOG_WAIT_SECONDS="${LOG_PROPAGATION_WAIT_SECONDS:-10}"
DRY_RUN=false
# 第一引数が --dry-run の場合は、AWS操作を行わず実行予定のコマンドを表示する。
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

runner=(uv run --project "${ROOT}/benchmark" python -m benchmark_runner)

execute() {
  # DRY_RUN が true の場合は副作用のあるコマンドを表示だけにし、それ以外は実行する。
  if "${DRY_RUN}"; then
    printf 'ドライラン:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

for memory in ${MEMORIES}; do
  # 現在のメモリ量をLambda設定へ反映してから、同じ条件で全ワークロードを実行する。
  execute terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
    -var="lambda_memory_mb=${memory}"

  # ドライラン時は未作成のTerraform出力を参照できないため、後続コマンド用のプレースホルダーを使う。
  # 実行時はTerraform出力からAPI URLとCloudWatchロググループ名を取得する。
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
    # メモリ量・ワークロード・開始時刻を含む一意な実行IDを作り、両実装の計測を相関できるようにする。
    run_id="arm64-${memory}-${workload}-$(date -u +%Y%m%dT%H%M%SZ)"
    execute "${runner[@]}" run --target both --workload "${workload}" \
      --run-id "${run_id}" --region "${REGION}" --architecture "${ARCHITECTURE}" \
      --memory-mb "${memory}" --python-endpoint "${python_endpoint}" \
      --rust-endpoint "${rust_endpoint}"
    # 実行時だけCloudWatchへのログ反映を待ち、ドライランでは待機時間を発生させない。
    if ! "${DRY_RUN}"; then
      sleep "${LOG_WAIT_SECONDS}"
    fi
    execute "${runner[@]}" collect --run-id "${run_id}" --region "${REGION}" \
      --python-log-group "${python_log_group}" --rust-log-group "${rust_log_group}"
    execute "${runner[@]}" report --run-id "${run_id}"
  done
done
