#!/usr/bin/env bash
# 比較対象の言語実装の一覧（spec/implementations.yaml）を、各スクリプトから読むための関数。
# 正本をYAMLのまま扱うため、解析はbenchmark_runnerのCLIに任せる。

IMPLEMENTATIONS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

implementation_cli() {
  uv run --quiet --project "${IMPLEMENTATIONS_ROOT}/benchmark" \
    python -m benchmark_runner implementations "$@" 2>/dev/null
}

# 実装キーを表示順に空白区切りで出力する。IMPLEMENTATIONSが指定されていればそれを優先する。
implementation_keys() {
  if [[ -n "${IMPLEMENTATIONS:-}" ]]; then
    printf '%s\n' "${IMPLEMENTATIONS}"
  else
    implementation_cli --format keys | tr '\n' ' '
  fi
}

# 指定した実装キーのアプリのディレクトリ（リポジトリ直下からの相対パス）を出力する。
implementation_app_dir() {
  local key="$1"
  implementation_cli --format tsv | awk -F '\t' -v key="${key}" '$1 == key { print $2 }'
}

# 全言語のイメージの元になるファイルを、一行一つのGit pathspecで出力する。
# イメージに入らないテスト（test_paths）は除外の指定（:(exclude)...）として含む。
implementation_image_pathspecs() {
  implementation_cli --format pathspecs
}
