#!/usr/bin/env bash
# 全言語のイメージを同じタグでビルド・ECR登録し、Lambdaへ渡すdigest固定URIを
# Terraformが自動で読み込む images.auto.tfvars.json へ書き出す。
#
# タグには公開時のGitコミット（-g<sha>）を含め、アプリに未コミットの変更があれば -dirty を付ける。
# 計測前の版の確認（benchmark_runner.deployment）は、このタグからデプロイ済みの版を読み取る。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/implementations.sh
source "${ROOT}/scripts/lib/implementations.sh"
TERRAFORM_DIR="${TERRAFORM_DIR:-${ROOT}/infrastructure/terraform}"
REGION="${AWS_REGION:-ap-northeast-1}"
PLATFORM="${PLATFORM:-linux/arm64}"
IMAGES_FILE="${IMAGES_FILE:-${TERRAFORM_DIR}/images.auto.tfvars.json}"
DRY_RUN=false

# 第一引数が--dry-runなら外部変更を行わず、未知の引数があれば誤操作防止のため拒否する。
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
elif [[ $# -ne 0 ]]; then
  echo "使い方: $0 [--dry-run]" >&2
  exit 2
fi

KEYS="$(implementation_keys)"

source_revision_suffix() {
  # 公開するコードのコミットをタグに残し、イメージの元になるファイルに未コミットの変更があれば
  # その旨も残す。イメージに入らないテストの変更は、デプロイ済みの版との照合と同じく数えない。
  local revision pathspec pathspecs=()
  revision="$(git -C "${ROOT}" rev-parse --short=12 HEAD)"
  while IFS= read -r pathspec; do
    [[ -n "${pathspec}" ]] && pathspecs+=("${pathspec}")
  done < <(implementation_image_pathspecs)
  # 実装一覧を読めなければ判定範囲がリポジトリ全体に広がるため、公開を止める。
  if [[ ${#pathspecs[@]} -eq 0 ]]; then
    echo "実装一覧（spec/implementations.yaml）を読めません" >&2
    exit 2
  fi
  if [[ -n "$(git -C "${ROOT}" status --porcelain -- "${pathspecs[@]}")" ]]; then
    printf -- '-g%s-dirty' "${revision}"
  else
    printf -- '-g%s' "${revision}"
  fi
}

TAG="${TAG:-benchmark-$(date -u +%Y%m%dT%H%M%SZ)$(source_revision_suffix)}"

# タグがECRの許可文字と長さを満たさない場合は、ビルド・登録前に拒否する。
if [[ ! "${TAG}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "TAGには有効なECRイメージタグを指定してください" >&2
  exit 2
fi

repository_url() {
  # 実装キーに対応する環境変数、ドライラン用値、Terraform出力の順でECRリポジトリURLを決める。
  local implementation="$1"
  local variable_name
  variable_name="$(printf '%s' "${implementation}" | tr '[:lower:]' '[:upper:]')_ECR_REPOSITORY_URL"
  local configured_url="${!variable_name:-}"

  if [[ -n "${configured_url}" ]]; then
    printf '%s\n' "${configured_url}"
  elif "${DRY_RUN}"; then
    printf '<terraform:ecr_repository_urls.%s>\n' "${implementation}"
  else
    terraform -chdir="${TERRAFORM_DIR}" output -json ecr_repository_urls |
      jq -r --arg key "${implementation}" '.[$key] // empty'
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

  # 最終digestがsha256形式でなければ対象イメージを一意に固定できないため、デプロイ対象を書き出さない。
  [[ "${digest}" == sha256:* ]] || {
    echo "${repository}:${TAG} にlinux/${expected_architecture}のイメージが見つかりません" >&2
    exit 2
  }
  printf '%s\n' "${digest}"
}

# 全実装の登録先を先に確定し、一つでも欠ければビルドを始めない。
declare -a repository_env=()
for implementation in ${KEYS}; do
  repository="$(repository_url "${implementation}")"
  # ECRリポジトリがなければ、実装を一覧に足した後のbootstrap-ecrが済んでいない。
  if [[ -z "${repository}" ]]; then
    echo "${implementation} のECRリポジトリがありません。先に make bootstrap-ecr を実行してください。" >&2
    exit 2
  fi
  repository_env+=("$(printf '%s' "${implementation}" | tr '[:lower:]' '[:upper:]')_ECR_REPOSITORY_URL=${repository}")
done

echo "イメージタグ: ${TAG}"
# ドライランはビルド・pushの予定だけを表示し、実行時は両処理を順に行う。
if "${DRY_RUN}"; then
  TAG="${TAG}" bash "${ROOT}/scripts/build-images.sh" --dry-run
  env AWS_REGION="${REGION}" TAG="${TAG}" "${repository_env[@]}" \
    bash "${ROOT}/scripts/push-images.sh" --dry-run
else
  TAG="${TAG}" bash "${ROOT}/scripts/build-images.sh"
  env AWS_REGION="${REGION}" TAG="${TAG}" PUSH_QUIET="${PUSH_QUIET:-false}" "${repository_env[@]}" \
    bash "${ROOT}/scripts/push-images.sh"
fi

# 実装キーごとのdigest固定URIを集め、Terraformの変数 image_uris として書き出す。
image_uris="{}"
for entry in "${repository_env[@]}"; do
  repository="${entry#*=}"
  variable_name="${entry%%=*}"
  implementation="$(printf '%s' "${variable_name%_ECR_REPOSITORY_URL}" | tr '[:upper:]' '[:lower:]')"
  # ドライランではdigestの仮値を使い、実行時はECRから実digestを確認する。
  if "${DRY_RUN}"; then
    digest="<sha256:${implementation}-image-digest>"
  else
    digest="$(image_digest "${repository}")"
  fi
  image_uris="$(jq --arg key "${implementation}" --arg uri "${repository}@${digest}" \
    '. + {($key): $uri}' <<<"${image_uris}")"
done

if "${DRY_RUN}"; then
  printf 'ドライラン: %s へ書き出す内容\n' "${IMAGES_FILE}"
  jq '{image_uris: .}' <<<"${image_uris}"
else
  jq '{image_uris: .}' <<<"${image_uris}" >"${IMAGES_FILE}"
  printf '\nデプロイ対象のイメージを %s へ書き出しました。\n' "${IMAGES_FILE}"
fi
printf '次のコマンドで Lambda と API Gateway をデプロイできます。\nmake deploy\n'
