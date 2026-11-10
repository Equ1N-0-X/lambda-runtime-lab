#!/usr/bin/env bash
# メモリ量とワークロードの全組み合わせを、比較対象の全言語へ同一条件で実行・収集・報告する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
MEMORIES="${MEMORIES:-256 512 1024}"
WORKLOADS="${WORKLOADS:-health transform-small transform-large compute-sequential items aggregate-sequential aggregate-concurrent}"
ARCHITECTURE="${ARCHITECTURE:-arm64}"
REGION="${AWS_REGION:-ap-northeast-1}"
# Terraformの既定の環境名（dev）のまま適用すると、別名の環境として全リソースを作り直すため、
# デプロイ時（make deploy）と同じ環境名を必ず渡す。
ENVIRONMENT="${ENVIRONMENT:-benchmark}"
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
    -var="aws_region=${REGION}" \
    -var="environment=${ENVIRONMENT}" \
    -var="lambda_memory_mb=${memory}"

  # 接続先とロググループは、実装キーごとのTerraform出力から runner の引数へ変換する。
  # ドライラン時は未作成のTerraform出力を参照できないため、--terraform-dir で実行時に解決させる。
  for workload in ${WORKLOADS}; do
    # メモリ量・ワークロード・開始時刻を含む一意な実行IDを作り、全実装の計測を相関できるようにする。
    run_id="arm64-${memory}-${workload}-$(date -u +%Y%m%dT%H%M%SZ)"
    execute "${runner[@]}" run --target all --workload "${workload}" \
      --run-id "${run_id}" --region "${REGION}" --architecture "${ARCHITECTURE}" \
      --memory-mb "${memory}" --terraform-dir "${TERRAFORM_DIR}"
    # 実行時だけCloudWatchへのログ反映を待ち、ドライランでは待機時間を発生させない。
    if ! "${DRY_RUN}"; then
      sleep "${LOG_WAIT_SECONDS}"
    fi
    execute "${runner[@]}" collect --run-id "${run_id}" --region "${REGION}" \
      --terraform-dir "${TERRAFORM_DIR}"
    execute "${runner[@]}" report --run-id "${run_id}"
  done
done
