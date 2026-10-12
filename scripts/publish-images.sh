#!/usr/bin/env bash
# 両実装のイメージを同じタグでビルド・ECR登録し、Lambdaへ渡すdigest固定URIを表示する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
REGION="${AWS_REGION:-ap-northeast-1}"
TAG="${TAG:-benchmark-$(date -u +%Y%m%dT%H%M%SZ)}"
PLATFORM="${PLATFORM:-linux/arm64}"
DRY_RUN=false

# 第一引数が--dry-runなら外部変更を行わず、未知の引数があれば誤操作防止のため拒否する。
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
elif [[ $# -ne 0 ]]; then
  echo "使い方: $0 [--dry-run]" >&2
  exit 2
fi

# タグがECRの許可文字と長さを満たさない場合は、ビルド・登録前に拒否する。
if [[ ! "${TAG}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "TAGには有効なECRイメージタグを指定してください" >&2
  exit 2
fi

repository_url() {
  # 実装名を対応する環境変数へ変換し、明示値、ドライラン用値、Terraform出力の順でURLを決める。
  local implementation="$1"
  local variable_name
  case "${implementation}" in
    python) variable_name="PYTHON_ECR_REPOSITORY_URL" ;;
    rust) variable_name="RUST_ECR_REPOSITORY_URL" ;;
    *) echo "不明な実装名です: ${implementation}" >&2; exit 2 ;;
  esac
  local configured_url="${!variable_name:-}"

  # 環境変数にURLがあれば優先し、ドライランなら仮値、実行時はTerraform出力を取得する。
  if [[ -n "${configured_url}" ]]; then
    printf '%s\n' "${configured_url}"
  elif "${DRY_RUN}"; then
    printf '<terraform:%s_ecr_repository_url>\n' "${implementation}"
  else
    terraform -chdir="${TERRAFORM_DIR}" output -raw "${implementation}_ecr_repository_url"
  fi
}

image_digest() {
  # ECR上のタグから期待アーキテクチャに対応する単一イメージdigestを解決する。
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

  # 取得結果がOCI indexの場合は、linuxかつ期待アーキテクチャの子マニフェストだけを選ぶ。
  if [[ "${media_type}" == "application/vnd.oci.image.index.v1+json" ]]; then
    digest="$(jq -r --arg architecture "${expected_architecture}" '
      .images[0].imageManifest
      | fromjson
      | .manifests[]
      | select(.platform.os == "linux" and .platform.architecture == $architecture)
      | .digest
    ' <<<"${image}" | head -n 1)"
  fi

  # 最終digestがsha256形式でなければ対象イメージを一意に固定できないため、デプロイ案内を出さない。
  [[ "${digest}" == sha256:* ]] || {
    echo "${repository}:${TAG} にlinux/${expected_architecture}のイメージが見つかりません" >&2
    exit 2
  }
  printf '%s\n' "${digest}"
}

python_repository="$(repository_url python)"
rust_repository="$(repository_url rust)"

# ドライランはビルド・pushの予定だけを表示し、実行時は両処理を順に行う。
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

# ドライランではdigestの仮値を使い、実行時はECRから両実装の実digestを確認する。
if "${DRY_RUN}"; then
  python_digest='<sha256:python-image-digest>'
  rust_digest='<sha256:rust-image-digest>'
else
  python_digest="$(image_digest "${python_repository}")"
  rust_digest="$(image_digest "${rust_repository}")"
  [[ "${python_digest}" == sha256:* ]] || { echo "Pythonイメージのdigestが見つかりません" >&2; exit 2; }
  [[ "${rust_digest}" == sha256:* ]] || { echo "Rustイメージのdigestが見つかりません" >&2; exit 2; }
fi

printf '\n次のコマンドで Lambda と API Gateway をデプロイできます。\n'
printf 'export PYTHON_IMAGE_URI=%q\n' "${python_repository}@${python_digest}"
printf 'export RUST_IMAGE_URI=%q\n' "${rust_repository}@${rust_digest}"
printf 'make deploy\n'
