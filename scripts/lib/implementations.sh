#!/usr/bin/env bash
# 比較対象の言語実装の一覧（spec/implementations.yaml）を、各スクリプトから読むための関数。
# 正本をYAMLのまま扱うため、解析はbenchmark_runnerのCLIに任せる。
#
# 一覧を読めない・空・未知のキーのまま処理を続けると、ループが0回で終わり
# 「何も作らずに成功」になるため、各関数は非0で返す。呼び出し側は
# KEYS="$(implementation_keys)" のように代入で受け取り、set -e で止める。

IMPLEMENTATIONS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

implementation_cli() {
  # uvの警告は標準エラーへ出るため、標準出力だけを結果に使い、失敗したときだけ診断を示す。
  local errors status=0
  errors="$(mktemp)"
  uv run --quiet --project "${IMPLEMENTATIONS_ROOT}/benchmark" \
    python -m benchmark_runner implementations "$@" 2>"${errors}" || status=$?
  if [[ "${status}" -ne 0 ]]; then
    echo "実装一覧（spec/implementations.yaml）を読めませんでした（終了コード ${status}）:" >&2
    cat "${errors}" >&2
  fi
  rm -f "${errors}"
  return "${status}"
}

# 実装キーを表示順に空白区切りで出力する。IMPLEMENTATIONSが指定されていればそれを優先する。
# 指定されたキーは実装一覧と照合し、一覧にないキーや空の一覧は失敗にする。
implementation_keys() {
  local registered key requested
  registered="$(implementation_cli --format keys)" || return 1
  # 一覧が空なら、対象のない処理を成功として終えないよう止める。
  if [[ -z "${registered//[[:space:]]/}" ]]; then
    echo "実装一覧（spec/implementations.yaml）に実装がありません" >&2
    return 1
  fi
  requested="${IMPLEMENTATIONS:-${registered}}"
  for key in ${requested}; do
    # 一覧にないキーは誤記の可能性が高く、存在しないディレクトリをビルドしようとするため止める。
    if ! grep -qxF -- "${key}" <<<"${registered}"; then
      echo "実装一覧にない実装キーです: ${key}" >&2
      return 1
    fi
  done
  # shellcheck disable=SC2086 # 空白区切りの一覧を一行に正規化するため、意図して分割する。
  echo ${requested}
}

# 指定した実装キーのアプリのディレクトリ（リポジトリ直下からの相対パス）を出力する。
implementation_app_dir() {
  local key="$1" listing
  listing="$(implementation_cli --format tsv)" || return 1
  awk -F '\t' -v key="${key}" '$1 == key { print $2 }' <<<"${listing}"
}

# 全言語のイメージの元になるファイルを、一行一つのGit pathspecで出力する。
# イメージに入らないテスト（test_paths）は除外の指定（:(exclude)...）として含む。
implementation_image_pathspecs() {
  implementation_cli --format pathspecs
}
