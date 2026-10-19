#!/usr/bin/env bash
# 固定した同一のOS専用ベースイメージから、比較対象の全言語のLambdaイメージを作成する。
#
# ベースdigestを一度だけ解決して全ビルドへ渡し、al2023タグの世代差がイメージサイズや
# コールドスタート比較へ混入しないようにする。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/implementations.sh
source "${ROOT}/scripts/lib/implementations.sh"
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
  # FINAL_BASEが明示された場合はその参照を使い、未指定ならタグから現在のdigestを一度だけ解決する。
  if [[ -n "${FINAL_BASE:-}" ]]; then
    echo "${FINAL_BASE}"
    return
  fi
  local digest
  digest="$(docker buildx imagetools inspect \
    "${FINAL_BASE_REPOSITORY}:${FINAL_BASE_TAG}" \
    --format '{{.Manifest.Digest}}')"
  # digestが空なら比較条件を固定できないため、どのイメージもビルドせず終了する。
  [[ -n "${digest}" ]] || { echo "ベースイメージのdigestを解決できません" >&2; exit 1; }
  echo "${FINAL_BASE_REPOSITORY}@${digest}"
}

build_image() {
  # 指定実装のイメージを作成し、ビルド時間・サイズ・使用ベースを結果JSONへ保存する。
  local implementation="$1"
  local final_base="$2"
  local image="lambda-runtime-lab-${implementation}:${TAG}"
  local app_dir
  app_dir="$(implementation_app_dir "${implementation}")"
  # 一覧にない実装名なら、どのディレクトリをビルドするか決められない。
  [[ -n "${app_dir}" ]] || { echo "実装一覧にない実装です: ${implementation}" >&2; exit 2; }
  local dockerfile="${ROOT}/${app_dir}/Dockerfile"
  local context="${ROOT}/${app_dir}"
  local output="${ROOT}/results/raw/${RUN_ID}/${implementation}-build.json"
  # Lambdaは単一イメージマニフェストを受け付けるため、証明書付きOCI indexを作らず、
  # pushしたタグをそのままLambdaイメージURIに使えるようprovenanceを無効化する。
  local command=(docker buildx build --progress "${BUILD_PROGRESS}" --platform "${PLATFORM}" --provenance=false --load
    --build-arg "FINAL_BASE=${final_base}"
    --build-arg "BUILD_DATE=${BUILD_DATE}"
    --build-arg "GIT_REVISION=${GIT_REVISION}"
    --tag "${image}" --file "${dockerfile}" "${context}")

  # ドライラン時はDocker・ファイル出力を行わず、予定コマンドだけを表示して戻る。
  if "${DRY_RUN}"; then
    printf 'ドライラン:'
    printf ' %q' "${command[@]}"
    printf '\n'
    return
  fi

  # Dockerfileが存在する場合だけビルドし、実装名誤りによる別コンテキストの利用を防ぐ。
  [[ -f "${dockerfile}" ]] || { echo "Dockerfileが見つかりません: ${dockerfile}" >&2; exit 2; }
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

# ドライランではネットワーク解決せず表示用参照を使い、実行時だけdigestを解決して全ビルドへ渡す。
if "${DRY_RUN}"; then
  FINAL_BASE_RESOLVED="${FINAL_BASE:-${FINAL_BASE_REPOSITORY}:${FINAL_BASE_TAG}}"
else
  FINAL_BASE_RESOLVED="$(resolve_final_base)"
  echo "共通ベースイメージ: ${FINAL_BASE_RESOLVED}"
fi

for implementation in $(implementation_keys); do
  build_image "${implementation}" "${FINAL_BASE_RESOLVED}"
done
