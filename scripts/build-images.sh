#!/usr/bin/env bash
# Builds both Lambda images from the same pinned OS-only base image.
#
# The base digest is resolved once and passed to both builds so that image size
# and cold start comparisons cannot be skewed by the two images picking up
# different revisions of the al2023 tag.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM="${PLATFORM:-linux/arm64}"
TAG="${TAG:-benchmark}"
RUN_ID="${RUN_ID:-image-build-$(date -u +%Y%m%dT%H%M%SZ)}"
FINAL_BASE_REPOSITORY="${FINAL_BASE_REPOSITORY:-public.ecr.aws/lambda/provided}"
FINAL_BASE_TAG="${FINAL_BASE_TAG:-al2023}"
BUILD_DATE="${BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
GIT_REVISION="${GIT_REVISION:-$(git -C "${ROOT}" rev-parse --verify --quiet HEAD || echo unknown)}"
BUILD_PROGRESS="${BUILD_PROGRESS:-auto}"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

resolve_final_base() {
  if [[ -n "${FINAL_BASE:-}" ]]; then
    echo "${FINAL_BASE}"
    return
  fi
  local digest
  digest="$(docker buildx imagetools inspect \
    "${FINAL_BASE_REPOSITORY}:${FINAL_BASE_TAG}" \
    --format '{{.Manifest.Digest}}')"
  [[ -n "${digest}" ]] || { echo "cannot resolve the base image digest" >&2; exit 1; }
  echo "${FINAL_BASE_REPOSITORY}@${digest}"
}

build_image() {
  local implementation="$1"
  local final_base="$2"
  local image="lambda-runtime-lab-${implementation}:${TAG}"
  local dockerfile="${ROOT}/apps/${implementation}/Dockerfile"
  local context="${ROOT}/apps/${implementation}"
  local output="${ROOT}/results/raw/${RUN_ID}/${implementation}-build.json"
  # Lambda accepts a single image manifest, but Buildx provenance produces an
  # OCI image index with an attestation manifest.  Disable provenance so the
  # pushed tag is directly usable as a Lambda image URI.
  local command=(docker buildx build --progress "${BUILD_PROGRESS}" --platform "${PLATFORM}" --provenance=false --load
    --build-arg "FINAL_BASE=${final_base}"
    --build-arg "BUILD_DATE=${BUILD_DATE}"
    --build-arg "GIT_REVISION=${GIT_REVISION}"
    --tag "${image}" --file "${dockerfile}" "${context}")

  if "${DRY_RUN}"; then
    printf 'DRY-RUN:'
    printf ' %q' "${command[@]}"
    printf '\n'
    return
  fi

  [[ -f "${dockerfile}" ]] || { echo "missing Dockerfile: ${dockerfile}" >&2; exit 2; }
  mkdir -p "$(dirname "${output}")"
  local started ended duration image_id size
  started="$(date +%s)"
  "${command[@]}"
  ended="$(date +%s)"
  duration="$((ended - started))"
  image_id="$(docker image inspect --format '{{.Id}}' "${image}")"
  size="$(docker image inspect --format '{{.Size}}' "${image}")"
  python3 - "${output}" "${implementation}" "${image}" "${image_id}" "${size}" \
    "${duration}" "${PLATFORM}" "${final_base}" "${GIT_REVISION}" "${BUILD_DATE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "implementation": sys.argv[2],
    "image": sys.argv[3],
    "image_id": sys.argv[4],
    "total_size_bytes": int(sys.argv[5]),
    "build_duration_seconds": int(sys.argv[6]),
    "platform": sys.argv[7],
    "final_base_image": sys.argv[8],
    "git_revision": sys.argv[9],
    "build_date": sys.argv[10],
}
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if "${DRY_RUN}"; then
  FINAL_BASE_RESOLVED="${FINAL_BASE:-${FINAL_BASE_REPOSITORY}:${FINAL_BASE_TAG}}"
else
  FINAL_BASE_RESOLVED="$(resolve_final_base)"
  echo "shared base image: ${FINAL_BASE_RESOLVED}"
fi

for implementation in ${IMPLEMENTATIONS:-python rust}; do
  build_image "${implementation}" "${FINAL_BASE_RESOLVED}"
done
