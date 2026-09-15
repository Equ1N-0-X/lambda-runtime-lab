#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
REGION="${AWS_REGION:-ap-northeast-1}"
ENVIRONMENT="${ENVIRONMENT:-benchmark}"
TF_AUTO_APPROVE="${TF_AUTO_APPROVE:-}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 2
fi

execute() {
  if "${DRY_RUN}"; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Lambda cannot be created until its container image exists, while the image
# cannot be pushed until the Terraform-managed ECR repository exists.
execute terraform -chdir="${TERRAFORM_DIR}" init
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
