#!/usr/bin/env bash
# ローカルの各言語のイメージを、対応するECRリポジトリへ認証・タグ付けして登録する。
# 登録先は実装キーごとの環境変数 <KEY大文字>_ECR_REPOSITORY_URL で受け取る。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/implementations.sh
source "${ROOT}/scripts/lib/implementations.sh"
REGION="${AWS_REGION:-ap-northeast-1}"
TAG="${TAG:-benchmark}"
DRY_RUN=false
PUSH_QUIET="${PUSH_QUIET:-false}"
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

repository_for() {
  local variable_name
  variable_name="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')_ECR_REPOSITORY_URL"
  printf '%s' "${!variable_name:-}"
}

keys="$(implementation_keys)"
missing=()
for implementation in ${keys}; do
  # 登録先がない実装を集め、実行時は一つでも欠ければ一部だけ登録する状態を作らない。
  [[ -n "$(repository_for "${implementation}")" ]] || missing+=("$(printf '%s' "${implementation}" | tr '[:lower:]' '[:upper:]')_ECR_REPOSITORY_URL")
done
if ! "${DRY_RUN}" && [[ ${#missing[@]} -gt 0 ]]; then
  echo "${missing[*]} の指定が必要です" >&2
  exit 2
fi

# 認証済みの登録先（空白区切り）。macOS標準のbash 3.2でも動くよう、連想配列は使わない。
LOGGED_IN_REGISTRIES=""

push_image() {
  # 一つの実装について、ECR認証、リモート用タグ付け、pushを順番に行う。
  local implementation="$1"
  local repository="$2"
  local local_image="lambda-runtime-lab-${implementation}:${TAG}"
  local destination="${repository:-<${implementation}_ECR_REPOSITORY_URL>}:${TAG}"
  local registry="${repository%%/*}"

  # ドライラン時は認証やpushを行わず、秘密値を表示しない形で予定コマンドだけを出力する。
  if "${DRY_RUN}"; then
    printf 'ドライラン: docker tag %q %q\n' "${local_image}" "${destination}"
    printf 'ドライラン: aws ecr get-login-password --region %q | docker login ... %q\n' \
      "${REGION}" "${registry:-<registry>}"
    printf 'ドライラン: docker push %q\n' "${destination}"
    return
  fi

  # 同じ登録先（レジストリ）には一度だけ認証し、別の登録先にはそれぞれ認証する。
  if [[ " ${LOGGED_IN_REGISTRIES} " != *" ${registry} "* ]]; then
    aws ecr get-login-password --region "${REGION}" |
      docker login --username AWS --password-stdin "${registry}"
    LOGGED_IN_REGISTRIES+=" ${registry}"
  fi
  docker tag "${local_image}" "${destination}"
  # PUSH_QUIETがtrueの場合は進捗を抑制し、それ以外はDocker標準の進捗を表示する。
  if "${PUSH_QUIET}"; then
    docker push --quiet "${destination}"
  else
    docker push "${destination}"
  fi
}

for implementation in ${keys}; do
  push_image "${implementation}" "$(repository_for "${implementation}")"
done
