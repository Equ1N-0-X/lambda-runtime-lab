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

# 公開するイメージは linux/arm64 だけでビルドしており（scripts/build-images.sh の PLATFORM）、
# 料金表・イメージ・実設定の組み合わせを確かめているのも arm64 だけである。
# 他の方式を指定すると、記録だけが実体と違う計測になるため、適用前に止める。
if [[ "${ARCHITECTURE}" != "arm64" ]]; then
  echo "ARCHITECTURE=${ARCHITECTURE} の一括計測には対応していません。対応しているのは arm64 だけです。" >&2
  exit 2
fi

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

# 最後にLambdaへ適用したメモリ。終了時に、次の計測で期待する設定とのずれを案内するために使う。
APPLIED_MEMORY=""
# 適用を試みたメモリ。適用の途中で失敗した場合は、実設定がどちらか分からないことを案内する。
ATTEMPTED_MEMORY=""

report_final_settings() {
  # 正常終了・失敗のどちらでも、Lambdaに残った設定と次の操作を示す。Terraformは自動で再適用しない
  # （全体の再適用は意図しない変更まで反映しうるため、利用者が確認してから行う）。
  local status=$?
  if "${DRY_RUN}" || [[ -z "${ATTEMPTED_MEMORY}" ]]; then
    return "${status}"
  fi
  # 適用中に失敗した場合は、Lambdaの実設定を確かめてから次の操作を選んでもらう。
  if [[ "${ATTEMPTED_MEMORY}" != "${APPLIED_MEMORY}" ]]; then
    echo "メモリ ${ATTEMPTED_MEMORY} MB の適用中に失敗したため、Lambda の実設定が分かりません。make ui の画面か aws lambda get-function-configuration で確認してください。" >&2
    return "${status}"
  fi
  echo "一括計測を終了しました（終了コード ${status}）。Lambda のメモリは最後に適用した ${APPLIED_MEMORY} MB のままです。" >&2
  # 次に使う設定（.env の LAMBDA_MEMORY_MB）が残った設定と違えば、画面からの計測は開始できない。
  if [[ -n "${LAMBDA_MEMORY_MB:-}" && "${LAMBDA_MEMORY_MB}" != "${APPLIED_MEMORY}" ]]; then
    echo "LAMBDA_MEMORY_MB=${LAMBDA_MEMORY_MB} と異なるため、このままでは画面の計測を開始できません。" >&2
  fi
  echo "既定のメモリへ戻すには make deploy、別のメモリにするには .env の LAMBDA_MEMORY_MB を実設定に合わせてください。" >&2
  return "${status}"
}
trap report_final_settings EXIT

for memory in ${MEMORIES}; do
  # 現在のメモリ量をLambda設定へ反映してから、同じ条件で全ワークロードを実行する。
  ATTEMPTED_MEMORY="${memory}"
  execute terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
    -var="aws_region=${REGION}" \
    -var="environment=${ENVIRONMENT}" \
    -var="architecture=${ARCHITECTURE}" \
    -var="lambda_memory_mb=${memory}"
  APPLIED_MEMORY="${memory}"

  # 接続先とロググループは、実装キーごとのTerraform出力から runner の引数へ変換する。
  # ドライラン時は未作成のTerraform出力を参照できないため、--terraform-dir で実行時に解決させる。
  for workload in ${WORKLOADS}; do
    # 方式・メモリ量・ワークロード・開始時刻を含む一意な実行IDを作り、全実装の計測を相関できるようにする。
    # 記録する方式とメモリは、runnerが計測前にLambdaの実設定と照合する（食い違えば計測しない）。
    run_id="${ARCHITECTURE}-${memory}-${workload}-$(date -u +%Y%m%dT%H%M%SZ)"
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
