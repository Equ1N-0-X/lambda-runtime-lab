#!/usr/bin/env bash
# ローカル既定URL向けに、DynamoDB・Python API・Rust Lambda RIEを準備して契約テストを実行する。
# 自身が起動したプロセスとコンテナだけを終了時に片付け、利用者が先に起動したサービスは維持する。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_API_URL="${PYTHON_API_URL:-}"
RUST_API_URL="${RUST_API_URL:-}"
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-}"
DYNAMODB_ENDPOINT_URL="${DYNAMODB_ENDPOINT_URL:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
CONTRACT_TRANSPORT="${CONTRACT_TRANSPORT:-direct}"
PYTHON_CONTRACT_TRANSPORT="${PYTHON_CONTRACT_TRANSPORT:-}"
RUST_CONTRACT_TRANSPORT="${RUST_CONTRACT_TRANSPORT:-}"
RUST_IMAGE="${RUST_IMAGE:-lambda-runtime-lab-rust:local}"
DYNAMODB_CONTAINER="lambda-runtime-lab-contract-dynamodb"
RUST_CONTAINER="lambda-runtime-lab-contract-rust"
RUST_HEALTH_EVENT='{"version":"2.0","routeKey":"$default","rawPath":"/health","rawQueryString":"","headers":{},"requestContext":{"accountId":"123456789012","apiId":"local","domainName":"localhost","domainPrefix":"local","http":{"method":"GET","path":"/health","protocol":"HTTP/1.1","sourceIp":"127.0.0.1","userAgent":"contract-test"},"requestId":"health-check","routeKey":"$default","stage":"$default","time":"01/Jan/2026:00:00:00 +0000","timeEpoch":1767225600000},"body":null,"isBase64Encoded":false}'
PYTHON_LOG=""
PYTHON_PID=""
STARTED_DYNAMODB=false
STARTED_RUST=false

use_local_environment() {
  # 値未指定または標準ローカル値だけなら環境を所有し、AWSや任意URLへの意図しない書き込みや停止を防ぐ。
  [[ -z "${PYTHON_API_URL}" || "${PYTHON_API_URL}" == "http://127.0.0.1:9001" ]] \
    && [[ -z "${RUST_API_URL}" || "${RUST_API_URL}" == "http://127.0.0.1:9002" ]] \
    && [[ -z "${DYNAMODB_TABLE_NAME}" || "${DYNAMODB_TABLE_NAME}" == "lambda-runtime-lab-contract" ]] \
    && [[ -z "${DYNAMODB_ENDPOINT_URL}" || "${DYNAMODB_ENDPOINT_URL}" == "http://127.0.0.1:8000" ]]
}

configure_target() {
  # 標準ローカル値ならRIE経由のRust呼び出しを選び、それ以外の指定時はCONTRACT_TRANSPORTの既定を維持する。
  if use_local_environment; then
    PYTHON_API_URL="http://127.0.0.1:9001"
    RUST_API_URL="http://127.0.0.1:9002"
    DYNAMODB_TABLE_NAME="lambda-runtime-lab-contract"
    DYNAMODB_ENDPOINT_URL="http://127.0.0.1:8000"
    PYTHON_CONTRACT_TRANSPORT="${PYTHON_CONTRACT_TRANSPORT:-${CONTRACT_TRANSPORT}}"
    RUST_CONTRACT_TRANSPORT="${RUST_CONTRACT_TRANSPORT:-lambda-rie}"
  else
    PYTHON_CONTRACT_TRANSPORT="${PYTHON_CONTRACT_TRANSPORT:-${CONTRACT_TRANSPORT}}"
    RUST_CONTRACT_TRANSPORT="${RUST_CONTRACT_TRANSPORT:-${CONTRACT_TRANSPORT}}"
  fi
}

cleanup() {
  local exit_status=$?

  # このスクリプトが起動したPythonだけを終了し、利用者が起動済みの9001番を停止しない。
  if [[ -n "${PYTHON_PID}" ]] && kill -0 "${PYTHON_PID}" 2>/dev/null; then
    kill "${PYTHON_PID}" || true
    wait "${PYTHON_PID}" 2>/dev/null || true
  fi

  # 起動責任がこのスクリプトにあるコンテナだけを削除し、既存コンテナの状態は変更しない。
  if [[ "${STARTED_RUST}" == true ]]; then
    docker rm --force "${RUST_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${STARTED_DYNAMODB}" == true ]]; then
    docker rm --force "${DYNAMODB_CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PYTHON_LOG}" ]]; then
    rm -f "${PYTHON_LOG}"
  fi

  exit "${exit_status}"
}

show_failure_context() {
  # 起動待機が尽きた場合は、サービス別ログを表示して接続拒否だけで原因を隠さない。
  if [[ -n "${PYTHON_LOG}" ]] && [[ -f "${PYTHON_LOG}" ]]; then
    echo "Python APIログ:" >&2
    cat "${PYTHON_LOG}" >&2
  fi
  docker logs "${RUST_CONTAINER}" 2>&1 || true
  docker logs "${DYNAMODB_CONTAINER}" 2>&1 || true
}

require_command() {
  local command_name="$1"
  # 必須コマンドの有無で分岐し、不足時は後続の曖昧な失敗を避けて導入対象を表示する。
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "contract-testには ${command_name} が必要です" >&2
    exit 2
  fi
}

wait_for_dynamodb() {
  local attempt
  for attempt in $(seq 1 30); do
    # DynamoDB APIが応答した場合だけ、テーブル作成とfixture投入へ進む。
    if AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
      aws dynamodb list-tables --endpoint-url "${DYNAMODB_ENDPOINT_URL}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  show_failure_context
  echo "DynamoDB Localの起動を確認できませんでした: ${DYNAMODB_ENDPOINT_URL}" >&2
  return 1
}

wait_for_python() {
  local attempt
  for attempt in $(seq 1 30); do
    # /healthが200の場合だけ、DynamoDBを使う契約要求を送れるPython APIとして扱う。
    if curl --connect-timeout 1 --max-time 3 --fail --silent "${PYTHON_API_URL}/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  show_failure_context
  echo "Python APIの起動を確認できませんでした: ${PYTHON_API_URL}" >&2
  return 1
}

rust_is_ready() {
  local response
  # RIE invoke APIのレスポンスを確認し、HTTPポートが開いているだけでは起動完了とみなさない。
  response="$(curl --connect-timeout 1 --max-time 3 --silent --request POST --header 'Content-Type: application/json' \
    --data "${RUST_HEALTH_EVENT}" "${RUST_API_URL}/2015-03-31/functions/function/invocations" || true)"
  jq --exit-status '.statusCode == 200' >/dev/null <<<"${response}"
}

wait_for_rust() {
  local attempt
  for attempt in $(seq 1 30); do
    # RIE invoke APIがLambdaプロキシの200を返した場合だけ、Rustランタイムを利用可能と判定する。
    if rust_is_ready; then
      return 0
    fi
    sleep 1
  done
  show_failure_context
  echo "Rust Lambda RIEの起動を確認できませんでした: ${RUST_API_URL}" >&2
  return 1
}

start_dynamodb_if_needed() {
  # 既定URLでDynamoDBが未起動なら専用コンテナを起動し、それ以外のURLは利用者管理の接続先として扱う。
  if AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
    aws dynamodb list-tables --endpoint-url "${DYNAMODB_ENDPOINT_URL}" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${DYNAMODB_ENDPOINT_URL}" != "http://127.0.0.1:8000" ]]; then
    echo "DynamoDBへ接続できません: ${DYNAMODB_ENDPOINT_URL}" >&2
    exit 1
  fi

  docker run --detach --rm --name "${DYNAMODB_CONTAINER}" --publish 127.0.0.1:8000:8000 \
    amazon/dynamodb-local:latest >/dev/null
  STARTED_DYNAMODB=true
  wait_for_dynamodb
}

prepare_fixtures() {
  # テーブルが無い場合だけ作成し、fixtureはID上書きで毎回正本の内容へそろえる。
  if ! AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
    aws dynamodb describe-table --endpoint-url "${DYNAMODB_ENDPOINT_URL}" \
      --table-name "${DYNAMODB_TABLE_NAME}" >/dev/null 2>&1; then
    AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
      aws dynamodb create-table --endpoint-url "${DYNAMODB_ENDPOINT_URL}" \
        --table-name "${DYNAMODB_TABLE_NAME}" \
        --attribute-definitions AttributeName=id,AttributeType=S \
        --key-schema AttributeName=id,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST >/dev/null
  fi

  AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
    uv run --project "${ROOT}/tests/contract" python "${ROOT}/tests/contract/seed_fixtures.py" \
      --table "${DYNAMODB_TABLE_NAME}" --endpoint-url "${DYNAMODB_ENDPOINT_URL}" --region "${AWS_REGION}"
}

start_python_if_needed() {
  # 既定URLのPython APIが未起動なら、この実行専用のバックグラウンドプロセスとして開始する。
  if curl --connect-timeout 1 --max-time 3 --fail --silent "${PYTHON_API_URL}/health" >/dev/null; then
    return 0
  fi
  if [[ "${PYTHON_API_URL}" != "http://127.0.0.1:9001" ]]; then
    echo "Python APIへ接続できません: ${PYTHON_API_URL}" >&2
    exit 1
  fi

  PYTHON_LOG="$(mktemp -t lambda-runtime-lab-contract-python.XXXXXX.log)"
  AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
    AWS_ENDPOINT_URL_DYNAMODB="${DYNAMODB_ENDPOINT_URL}" DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME}" \
    uv run --project "${ROOT}/apps/python" --with uvicorn \
      uvicorn app.main:app --app-dir "${ROOT}/apps/python/src" --host 127.0.0.1 --port 9001 \
      >"${PYTHON_LOG}" 2>&1 &
  PYTHON_PID=$!
  wait_for_python
}

start_rust_if_needed() {
  # 既定URLのRIEが未起動ならイメージを確認してから開始し、外部URLは利用者管理として起動しない。
  if rust_is_ready >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${RUST_API_URL}" != "http://127.0.0.1:9002" ]]; then
    echo "Rust Lambda RIEへ接続できません: ${RUST_API_URL}" >&2
    exit 1
  fi
  if ! docker image inspect "${RUST_IMAGE}" >/dev/null 2>&1; then
    make -C "${ROOT}" build-rust
  fi

  # Linux Dockerではhost.docker.internalを明示解決し、macOS/WindowsではDocker Desktopの標準名を使う。
  if [[ "$(uname -s)" == Linux ]]; then
    docker run --detach --rm --name "${RUST_CONTAINER}" --publish 127.0.0.1:9002:8080 \
      --add-host host.docker.internal:host-gateway \
      --env AWS_ACCESS_KEY_ID=test --env AWS_SECRET_ACCESS_KEY=test --env AWS_REGION="${AWS_REGION}" \
      --env AWS_ENDPOINT_URL_DYNAMODB=http://host.docker.internal:8000 \
      --env DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME}" "${RUST_IMAGE}" >/dev/null
  else
    docker run --detach --rm --name "${RUST_CONTAINER}" --publish 127.0.0.1:9002:8080 \
      --env AWS_ACCESS_KEY_ID=test --env AWS_SECRET_ACCESS_KEY=test --env AWS_REGION="${AWS_REGION}" \
      --env AWS_ENDPOINT_URL_DYNAMODB=http://host.docker.internal:8000 \
      --env DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME}" "${RUST_IMAGE}" >/dev/null
  fi
  STARTED_RUST=true
  wait_for_rust
}

main() {
  require_command curl
  require_command uv

  # ローカル自動実行時だけDocker・AWS CLI・jqを要求し、外部API検証へ不要な依存を持ち込まない。
  if use_local_environment; then
    require_command aws
    require_command docker
    require_command jq
    configure_target
    start_dynamodb_if_needed
    prepare_fixtures
    start_python_if_needed
    start_rust_if_needed
  else
    configure_target
  fi

  PYTHON_API_URL="${PYTHON_API_URL}" RUST_API_URL="${RUST_API_URL}" \
    PYTHON_CONTRACT_TRANSPORT="${PYTHON_CONTRACT_TRANSPORT}" \
    RUST_CONTRACT_TRANSPORT="${RUST_CONTRACT_TRANSPORT}" \
    uv run --project "${ROOT}/tests/contract" python -m pytest \
      "${ROOT}/tests/contract/test_contract.py" -m integration
}

trap cleanup EXIT
main "$@"
