#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
REGION="${AWS_REGION:-ap-northeast-1}"
TAG="${TAG:-benchmark-$(date -u +%Y%m%dT%H%M%SZ)}"
PLATFORM="${PLATFORM:-linux/arm64}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 2
fi

if [[ ! "${TAG}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "TAG must be a valid ECR image tag" >&2
  exit 2
fi

repository_url() {
  local implementation="$1"
  local variable_name
  case "${implementation}" in
    python) variable_name="PYTHON_ECR_REPOSITORY_URL" ;;
    rust) variable_name="RUST_ECR_REPOSITORY_URL" ;;
    *) echo "unknown implementation: ${implementation}" >&2; exit 2 ;;
  esac
  local configured_url="${!variable_name:-}"

  if [[ -n "${configured_url}" ]]; then
    printf '%s\n' "${configured_url}"
  elif "${DRY_RUN}"; then
    printf '<terraform:%s_ecr_repository_url>\n' "${implementation}"
  else
    terraform -chdir="${TERRAFORM_DIR}" output -raw "${implementation}_ecr_repository_url"
  fi
}

image_digest() {
  local repository="$1"
  local expected_architecture="${PLATFORM#*/}"
  local image
  local media_type
  local digest

  image="$(aws ecr batch-get-image \
    --region "${REGION}" \
    --repository-name "${repository##*/}" \
    --image-ids "imageTag=${TAG}" \
    --accepted-media-types application/vnd.oci.image.manifest.v1+json application/vnd.oci.image.index.v1+json application/vnd.docker.distribution.manifest.v2+json \
    --output json)"
  media_type="$(jq -r '.images[0].imageManifestMediaType // empty' <<<"${image}")"
  digest="$(jq -r '.images[0].imageId.imageDigest // empty' <<<"${image}")"

  if [[ "${media_type}" == "application/vnd.oci.image.index.v1+json" ]]; then
    digest="$(jq -r --arg architecture "${expected_architecture}" '
      .images[0].imageManifest
      | fromjson
      | .manifests[]
      | select(.platform.os == "linux" and .platform.architecture == $architecture)
      | .digest
    ' <<<"${image}" | head -n 1)"
  fi

  [[ "${digest}" == sha256:* ]] || {
    echo "No linux/${expected_architecture} image manifest was found for ${repository}:${TAG}" >&2
    exit 2
  }
  printf '%s\n' "${digest}"
}

python_repository="$(repository_url python)"
rust_repository="$(repository_url rust)"

if "${DRY_RUN}"; then
  TAG="${TAG}" bash "${ROOT}/scripts/build-images.sh" --dry-run
  AWS_REGION="${REGION}" \
  TAG="${TAG}" \
  PYTHON_ECR_REPOSITORY_URL="${python_repository}" \
  RUST_ECR_REPOSITORY_URL="${rust_repository}" \
  bash "${ROOT}/scripts/push-images.sh" --dry-run
else
TAG="${TAG}" bash "${ROOT}/scripts/build-images.sh"
  AWS_REGION="${REGION}" \
  TAG="${TAG}" \
  PUSH_QUIET="${PUSH_QUIET:-false}" \
  PYTHON_ECR_REPOSITORY_URL="${python_repository}" \
  RUST_ECR_REPOSITORY_URL="${rust_repository}" \
  bash "${ROOT}/scripts/push-images.sh"
fi

if "${DRY_RUN}"; then
  python_digest='<sha256:python-image-digest>'
  rust_digest='<sha256:rust-image-digest>'
else
  python_digest="$(image_digest "${python_repository}")"
  rust_digest="$(image_digest "${rust_repository}")"
  [[ "${python_digest}" == sha256:* ]] || { echo "Python image digest was not found" >&2; exit 2; }
  [[ "${rust_digest}" == sha256:* ]] || { echo "Rust image digest was not found" >&2; exit 2; }
fi

printf '\n次のコマンドで Lambda と API Gateway をデプロイできます。\n'
printf 'export PYTHON_IMAGE_URI=%q\n' "${python_repository}@${python_digest}"
printf 'export RUST_IMAGE_URI=%q\n' "${rust_repository}@${rust_digest}"
printf 'make deploy\n'
