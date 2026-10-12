# リポジトリ直下の env ファイルを現在のシェルへ一括 export する。
# 使い方:
#   source ./.env.export.sh           # .env（AWSデプロイ先）
#   source ./.env.export.sh --local   # .env.local（ローカル起動）
# ./ 実行や bash 直呼びでは呼び出し元シェルに残らない。

# source されていない場合は子プロセスで終わるため、使い方を案内して停止する。
if ! (return 0 2>/dev/null); then
  echo "使い方: source ./.env.export.sh [--local]" >&2
  exit 1
fi

# bash では BASH_SOURCE、zsh などでは $0 からこのスクリプトの場所を求める。
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  _env_export_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  _env_export_root="$(cd "$(dirname "$0")" && pwd)"
fi

_env_name=".env"
_env_example=".env.example"

# 引数で読み込む env を切り替える。未指定は AWS 向け .env、--local はローカル向け .env.local。
while [ "$#" -gt 0 ]; do
  case "$1" in
    --local)
      _env_name=".env.local"
      _env_example=".env.local.example"
      shift
      ;;
    -h|--help)
      echo "使い方: source ./.env.export.sh [--local]" >&2
      echo "  （なし）   .env を読み込む" >&2
      echo "  --local  .env.local を読み込む" >&2
      unset _env_export_root _env_name _env_example
      return 0
      ;;
    *)
      echo "不明な引数です: $1" >&2
      echo "使い方: source ./.env.export.sh [--local]" >&2
      unset _env_export_root _env_name _env_example
      return 2
      ;;
  esac
done

_env_file="${_env_export_root}/${_env_name}"

# 対象ファイルが無い場合は example からの作成を案内し、不完全な環境のまま続行しない。
if [ ! -f "${_env_file}" ]; then
  echo "${_env_name} がありません。先に cp ${_env_example} ${_env_name} を実行してください。" >&2
  unset _env_export_root _env_name _env_example _env_file
  return 1
fi

# 読込先を切り替える前にローカル専用のSDK上書きを消し、.envに項目が無い場合も前回のLocal設定を残さない。
unset DYNAMODB_ENDPOINT_URL AWS_ENDPOINT_URL_DYNAMODB

# KEY=VALUE 形式の代入を自動 export し、読み込み後は自動 export を解除する。
set -a
# shellcheck disable=SC1090
source "${_env_file}"
set +a

unset _env_export_root _env_name _env_example _env_file
