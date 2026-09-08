#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS="${ROOT}/results"
RUN_ID=""
ALL=false
YES=false
DRY_RUN=false

while (($#)); do
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --all) ALL=true; shift ;;
    --yes) YES=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if "${ALL}" && [[ -n "${RUN_ID}" ]]; then
  echo "choose either --all or --run-id" >&2
  exit 2
fi
if ! "${ALL}" && [[ -z "${RUN_ID}" ]]; then
  echo "--run-id ID or --all is required" >&2
  exit 2
fi
if [[ -n "${RUN_ID}" ]] && [[ ! "${RUN_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid run ID" >&2
  exit 2
fi
if "${ALL}" && ! "${YES}" && ! "${DRY_RUN}"; then
  echo "--all requires --yes" >&2
  exit 2
fi

remove_path() {
  local path="$1"
  [[ "${path}" == "${RESULTS}/"* ]] || { echo "unsafe path: ${path}" >&2; exit 2; }
  if "${DRY_RUN}"; then
    echo "DRY-RUN: remove ${path}"
  else
    rm -rf -- "${path}"
  fi
}

if "${ALL}"; then
  shopt -s nullglob dotglob
  for path in "${RESULTS}/raw/"* "${RESULTS}/reports/"*; do
    [[ "$(basename "${path}")" == ".gitkeep" ]] && continue
    remove_path "${path}"
  done
else
  remove_path "${RESULTS}/raw/${RUN_ID}"
  remove_path "${RESULTS}/reports/${RUN_ID}.md"
  remove_path "${RESULTS}/reports/${RUN_ID}.csv"
fi
