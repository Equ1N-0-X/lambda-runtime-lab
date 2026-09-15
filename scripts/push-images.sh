#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
TAG="${TAG:-benchmark}"
PYTHON_REPOSITORY="${PYTHON_ECR_REPOSITORY_URL:-}"
RUST_REPOSITORY="${RUST_ECR_REPOSITORY_URL:-}"
DRY_RUN=false
PUSH_QUIET="${PUSH_QUIET:-false}"
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if ! "${DRY_RUN}" && { [[ -z "${PYTHON_REPOSITORY}" ]] || [[ -z "${RUST_REPOSITORY}" ]]; }; then
  echo "PYTHON_ECR_REPOSITORY_URL and RUST_ECR_REPOSITORY_URL are required" >&2
  exit 2
fi

push_image() {
  local implementation="$1"
  local repository="$2"
  local local_image="lambda-runtime-lab-${implementation}:${TAG}"
  local destination="${repository:-<${implementation^^}_ECR_REPOSITORY_URL>}:${TAG}"
  local registry="${repository%%/*}"

  if "${DRY_RUN}"; then
    printf 'DRY-RUN: docker tag %q %q\n' "${local_image}" "${destination}"
    printf 'DRY-RUN: aws ecr get-login-password --region %q | docker login ... %q\n' \
      "${REGION}" "${registry:-<registry>}"
    printf 'DRY-RUN: docker push %q\n' "${destination}"
    return
  fi

  aws ecr get-login-password --region "${REGION}" |
    docker login --username AWS --password-stdin "${registry}"
  docker tag "${local_image}" "${destination}"
  if "${PUSH_QUIET}"; then
    docker push --quiet "${destination}"
  else
    docker push "${destination}"
  fi
}

push_image python "${PYTHON_REPOSITORY}"
push_image rust "${RUST_REPOSITORY}"
