#!/usr/bin/env bash
# ローカルのPython/Rustイメージを、対応するECRリポジトリへ認証・タグ付けして登録する。
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
TAG="${TAG:-benchmark}"
PYTHON_REPOSITORY="${PYTHON_ECR_REPOSITORY_URL:-}"
RUST_REPOSITORY="${RUST_ECR_REPOSITORY_URL:-}"
DRY_RUN=false
PUSH_QUIET="${PUSH_QUIET:-false}"
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# 実行時は両方の登録先を必須とし、ドライラン時だけプレースホルダーによる手順確認を許可する。
if ! "${DRY_RUN}" && { [[ -z "${PYTHON_REPOSITORY}" ]] || [[ -z "${RUST_REPOSITORY}" ]]; }; then
  echo "PYTHON_ECR_REPOSITORY_URL と RUST_ECR_REPOSITORY_URL の指定が必要です" >&2
  exit 2
fi

push_image() {
  # 一つの実装について、ECR認証、リモート用タグ付け、pushを順番に行う。
  local implementation="$1"
  local repository="$2"
  local local_image="lambda-runtime-lab-${implementation}:${TAG}"
  local destination="${repository:-<${implementation^^}_ECR_REPOSITORY_URL>}:${TAG}"
  local registry="${repository%%/*}"

  # ドライラン時は認証やpushを行わず、秘密値を表示しない形で予定コマンドだけを出力する。
  if "${DRY_RUN}"; then
    printf 'ドライラン: docker tag %q %q\n' "${local_image}" "${destination}"
    printf 'ドライラン: aws ecr get-login-password --region %q | docker login ... %q\n' \
      "${REGION}" "${registry:-<registry>}"
    printf 'ドライラン: docker push %q\n' "${destination}"
    return
  fi

  aws ecr get-login-password --region "${REGION}" |
    docker login --username AWS --password-stdin "${registry}"
  docker tag "${local_image}" "${destination}"
  # PUSH_QUIETがtrueの場合は進捗を抑制し、それ以外はDocker標準の進捗を表示する。
  if "${PUSH_QUIET}"; then
    docker push --quiet "${destination}"
  else
    docker push "${destination}"
  fi
}

push_image python "${PYTHON_REPOSITORY}"
push_image rust "${RUST_REPOSITORY}"
