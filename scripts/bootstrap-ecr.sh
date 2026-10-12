#!/usr/bin/env bash
# Lambda用コンテナを登録する前に、Terraform管理のECRリポジトリと取得ポリシーだけを作成する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
REGION="${AWS_REGION:-ap-northeast-1}"
ENVIRONMENT="${ENVIRONMENT:-benchmark}"
TF_AUTO_APPROVE="${TF_AUTO_APPROVE:-}"
DRY_RUN=false

# 第一引数が --dry-run の場合はTerraformを実行せず、実行予定のコマンドだけを表示する。
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
# --dry-run 以外の引数がある場合は、誤ったAWS操作を防ぐため使い方を表示して終了する。
elif [[ $# -ne 0 ]]; then
  echo "使い方: $0 [--dry-run]" >&2
  exit 2
fi

execute() {
  # DRY_RUN が true の場合は外部状態を変更せず、それ以外は受け取ったコマンドを実行する。
  if "${DRY_RUN}"; then
    printf 'ドライラン:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Lambda作成にはコンテナイメージが必要であり、イメージ登録にはTerraform管理のECRリポジトリが必要なため、
# 先にECR関連リソースだけを初期化・作成する。
execute terraform -chdir="${TERRAFORM_DIR}" init

# TF_AUTO_APPROVE が指定されている場合は確認なしで適用し、未指定ならTerraformの確認入力を受けて適用する。
if [[ -n "${TF_AUTO_APPROVE}" ]]; then
  execute terraform -chdir="${TERRAFORM_DIR}" apply "${TF_AUTO_APPROVE}" \
    -target=aws_ecr_repository.runtime \
    -target=aws_ecr_repository_policy.lambda_pull \
    -var="aws_region=${REGION}" \
    -var="environment=${ENVIRONMENT}"
else
  execute terraform -chdir="${TERRAFORM_DIR}" apply \
    -target=aws_ecr_repository.runtime \
    -target=aws_ecr_repository_policy.lambda_pull \
    -var="aws_region=${REGION}" \
    -var="environment=${ENVIRONMENT}"
fi
