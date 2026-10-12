#!/usr/bin/env bash
# 指定した比較実行の結果、または確認済みの全結果を安全条件付きで削除する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS="${ROOT}/results"
RUN_ID=""
ALL=false
YES=false
DRY_RUN=false

while (($#)); do
  # 引数名ごとに削除範囲・確認省略・ドライランを設定し、未知の指定は安全のため拒否する。
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --all) ALL=true; shift ;;
    --yes) YES=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "不明な引数です: $1" >&2; exit 2 ;;
  esac
done

# --all と --run-id が同時指定された場合は削除対象が曖昧なため、どちらか一方だけを要求する。
if "${ALL}" && [[ -n "${RUN_ID}" ]]; then
  echo "--all と --run-id は同時に指定できません" >&2
  exit 2
fi
# 全削除でなく実行IDもない場合は対象を決められないため、削除を開始しない。
if ! "${ALL}" && [[ -z "${RUN_ID}" ]]; then
  echo "--run-id ID または --all の指定が必要です" >&2
  exit 2
fi
# 実行IDがある場合は許可文字・長さを満たすものだけをパスへ使い、意図しない削除先を防ぐ。
if [[ -n "${RUN_ID}" ]] && [[ ! "${RUN_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "実行IDの形式が不正です" >&2
  exit 2
fi
# 全削除を実行する場合は、--yesで明示確認したときだけ許可する。ドライランは削除しないため除外する。
if "${ALL}" && ! "${YES}" && ! "${DRY_RUN}"; then
  echo "--all には --yes の指定が必要です" >&2
  exit 2
fi

remove_path() {
  # 削除先がresults配下にある場合だけ許可し、ドライランでは表示だけ、実行時は再帰削除する。
  local path="$1"
  [[ "${path}" == "${RESULTS}/"* ]] || { echo "安全でない削除先です: ${path}" >&2; exit 2; }
  if "${DRY_RUN}"; then
    echo "ドライラン: 削除 ${path}"
  else
    rm -rf -- "${path}"
  fi
}

# ALL が true の場合は.gitkeepを除く全結果を削除し、それ以外は指定実行IDに対応する三つの結果だけを削除する。
if "${ALL}"; then
  shopt -s nullglob dotglob
  for path in "${RESULTS}/raw/"* "${RESULTS}/reports/"*; do
    # 管理対象ディレクトリをGitに残す.gitkeepは、全削除でも保持する。
    [[ "$(basename "${path}")" == ".gitkeep" ]] && continue
    remove_path "${path}"
  done
else
  remove_path "${RESULTS}/raw/${RUN_ID}"
  remove_path "${RESULTS}/reports/${RUN_ID}.md"
  remove_path "${RESULTS}/reports/${RUN_ID}.csv"
fi
