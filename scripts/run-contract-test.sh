#!/usr/bin/env bash
# 比較対象の全言語について、DynamoDB Localと各言語のLambdaイメージ（Lambda RIE経由）を準備して
# 契約テストを実行する。言語の一覧は spec/implementations.yaml から読む。
#
# 全言語をデプロイするのと同じイメージで検証するため、どの言語もLambda RIEで起動する。
# <KEY>_API_URL がどれか一つでも指定されていれば、起動を行わず指定先だけを検証する。
# 自身が起動したコンテナだけを終了時に片付け、利用者が先に起動したサービスは維持する。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/implementations.sh
source "${ROOT}/scripts/lib/implementations.sh"
DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-}"
DYNAMODB_ENDPOINT_URL="${DYNAMODB_ENDPOINT_URL:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-local}"
# 言語ごとのRIEは、この番号から一覧の順に一つずつ割り当てたポートで待ち受ける。
FIRST_PORT="${CONTRACT_FIRST_PORT:-9101}"
DYNAMODB_CONTAINER="lambda-runtime-lab-contract-dynamodb"
HEALTH_EVENT='{"version":"2.0","routeKey":"$default","rawPath":"/health","rawQueryString":"","headers":{},"requestContext":{"accountId":"123456789012","apiId":"local","domainName":"localhost","domainPrefix":"local","http":{"method":"GET","path":"/health","protocol":"HTTP/1.1","sourceIp":"127.0.0.1","userAgent":"contract-test"},"requestId":"health-check","routeKey":"$default","stage":"$default","time":"01/Jan/2026:00:00:00 +0000","timeEpoch":1767225600000},"body":null,"isBase64Encoded":false}'
KEYS="$(implementation_keys)"
STARTED_DYNAMODB=false
STARTED_CONTAINERS=()

upper() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]'
}

url_variable() {
  printf '%s_API_URL' "$(upper "$1")"
}

use_local_environment() {
  # どの言語のURLも、DynamoDBも標準ローカル値以外が指定されていなければ環境を所有する。
  # AWSや任意URLへの意図しない書き込みや停止を防ぐ。
  local implementation variable
  for implementation in ${KEYS}; do
    variable="$(url_variable "${implementation}")"
    [[ -z "${!variable:-}" ]] || return 1
  done
  [[ -z "${DYNAMODB_TABLE_NAME}" || "${DYNAMODB_TABLE_NAME}" == "lambda-runtime-lab-contract" ]] \
    && [[ -z "${DYNAMODB_ENDPOINT_URL}" || "${DYNAMODB_ENDPOINT_URL}" == "http://127.0.0.1:8000" ]]
}

cleanup() {
  local exit_status=$?
  local container

  # 起動責任がこのスクリプトにあるコンテナだけを削除し、既存コンテナの状態は変更しない。
  # macOS標準のbash 3.2では空配列の展開が未定義変数扱いになるため、要素がある場合だけ展開する。
  for container in ${STARTED_CONTAINERS[@]+"${STARTED_CONTAINERS[@]}"}; do
    docker rm --force "${container}" >/dev/null 2>&1 || true
  done
  if [[ "${STARTED_DYNAMODB}" == true ]]; then
    docker rm --force "${DYNAMODB_CONTAINER}" >/dev/null 2>&1 || true
  fi

  exit "${exit_status}"
}

show_failure_context() {
  # 起動待機が尽きた場合は、サービス別ログを表示して接続拒否だけで原因を隠さない。
  local container
  for container in ${STARTED_CONTAINERS[@]+"${STARTED_CONTAINERS[@]}"}; do
    echo "${container} のログ:" >&2
    docker logs "${container}" 2>&1 || true
  done
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

runtime_is_ready() {
  local url="$1"
  local response
  # RIE invoke APIのレスポンスを確認し、HTTPポートが開いているだけでは起動完了とみなさない。
  response="$(curl --connect-timeout 1 --max-time 5 --silent --request POST \
    --header 'Content-Type: application/json' --data "${HEALTH_EVENT}" \
    "${url}/2015-03-31/functions/function/invocations" || true)"
  jq --exit-status '.statusCode == 200' >/dev/null 2>&1 <<<"${response}"
}

wait_for_runtime() {
  local label="$1"
  local url="$2"
  local attempt
  # 起動の遅い言語（JVMなど）も待てるよう、最大60秒まで確認を繰り返す。
  for attempt in $(seq 1 60); do
    if runtime_is_ready "${url}"; then
      return 0
    fi
    sleep 1
  done
  show_failure_context
  echo "${label} の Lambda RIE の起動を確認できませんでした: ${url}" >&2
  return 1
}

start_dynamodb_if_needed() {
  # 既定URLでDynamoDBが未起動なら専用コンテナを起動する。
  if AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION="${AWS_REGION}" \
    aws dynamodb list-tables --endpoint-url "${DYNAMODB_ENDPOINT_URL}" >/dev/null 2>&1; then
    return 0
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

start_runtime() {
  # 一つの言語のイメージを、本来の起動コマンドのままLambda RIEの下で起動する。
  local implementation="$1"
  local port="$2"
  local image="lambda-runtime-lab-${implementation}:${IMAGE_TAG}"
  local container="lambda-runtime-lab-contract-${implementation}"
  local url="http://127.0.0.1:${port}"
  local command_line

  # イメージがなければ、この言語だけをビルドしてから起動する。
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    make -C "${ROOT}" build IMPLEMENTATIONS="${implementation}" IMAGE_TAG="${IMAGE_TAG}"
  fi
  # イメージ本来のENTRYPOINTとCMDをRIEの引数に渡し、Lambdaと同じ起動経路を再現する。
  command_line="$(docker image inspect "${image}" \
    --format '{{range .Config.Entrypoint}}{{.}} {{end}}{{range .Config.Cmd}}{{.}} {{end}}')"

  docker rm --force "${container}" >/dev/null 2>&1 || true
  local host_options=()
  # Linux Dockerではhost.docker.internalを明示解決し、macOS/WindowsではDocker Desktopの標準名を使う。
  if [[ "$(uname -s)" == Linux ]]; then
    host_options=(--add-host host.docker.internal:host-gateway)
  fi
  # shellcheck disable=SC2086 # 起動コマンドは空白区切りの引数として渡す。
  docker run --detach --rm --name "${container}" --publish "127.0.0.1:${port}:8080" \
    ${host_options[@]+"${host_options[@]}"} \
    --env AWS_ACCESS_KEY_ID=test --env AWS_SECRET_ACCESS_KEY=test --env AWS_REGION="${AWS_REGION}" \
    --env AWS_ENDPOINT_URL_DYNAMODB=http://host.docker.internal:8000 \
    --env DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME}" \
    --entrypoint /usr/local/bin/aws-lambda-rie "${image}" ${command_line} >/dev/null
  STARTED_CONTAINERS+=("${container}")
  wait_for_runtime "${implementation}" "${url}"

  export "$(url_variable "${implementation}")=${url}"
  export "$(upper "${implementation}")_CONTRACT_TRANSPORT=lambda-rie"
}

main() {
  require_command curl
  require_command uv

  # ローカル自動実行時だけDocker・AWS CLI・jqを要求し、外部API検証へ不要な依存を持ち込まない。
  if use_local_environment; then
    require_command aws
    require_command docker
    require_command jq
    DYNAMODB_TABLE_NAME="lambda-runtime-lab-contract"
    DYNAMODB_ENDPOINT_URL="http://127.0.0.1:8000"
    start_dynamodb_if_needed
    prepare_fixtures
    local port="${FIRST_PORT}"
    local implementation
    for implementation in ${KEYS}; do
      start_runtime "${implementation}" "${port}"
      port=$((port + 1))
    done
  fi

  uv run --project "${ROOT}/tests/contract" python -m pytest \
    "${ROOT}/tests/contract/test_contract.py" -m integration
}

trap cleanup EXIT
main "$@"
