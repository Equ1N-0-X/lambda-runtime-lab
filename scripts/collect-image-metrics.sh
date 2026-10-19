#!/usr/bin/env bash
# 指定実行IDについて、ローカルイメージ・コンテナ内成果物・ECR登録情報をJSONへ収集する。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${RUN_ID:-}"
TAG="${TAG:-benchmark}"
REGION="${AWS_REGION:-ap-northeast-1}"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# 実行IDが空なら保存先を特定できないため、DockerやAWSへ問い合わせる前に停止する。
if [[ -z "${RUN_ID}" ]]; then
  echo "RUN_IDの指定が必要です" >&2
  exit 2
fi

# 対象の実装と、実装ごとに容量を測るコンテナ内のパスは実装一覧（spec/implementations.yaml）から読む。
IMPLEMENTATIONS_JSON="$(uv run --quiet --project "${ROOT}/benchmark" \
  python -m benchmark_runner implementations --format json 2>/dev/null)"

python3 - "${ROOT}" "${RUN_ID}" "${TAG}" "${REGION}" "${DRY_RUN}" "${IMPLEMENTATIONS_JSON}" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

root, run_id, tag, region, dry_run, implementations_json = sys.argv[1:]
output = Path(root) / "results" / "raw" / run_id / "image-metrics.json"
implementations = {item["key"]: item for item in json.loads(implementations_json)}
images = {key: f"lambda-runtime-lab-{key}:{tag}" for key in implementations}
repositories = {key: os.getenv(f"{key.upper()}_ECR_REPOSITORY_URL") for key in implementations}

# ドライランではDocker・ECRへアクセスせず、収集対象と出力先の計画だけを表示する。
if dry_run == "true":
    print(json.dumps({
        "dry_run": True,
        "output": str(output),
        "images": images,
        "region": region,
        "ecr_repositories": repositories,
    }, indent=2, sort_keys=True))
    raise SystemExit(0)

def run(command, *, check=True):
    """外部コマンドを実行し、後続のJSON解析に使う標準出力だけを返す。"""
    result = subprocess.run(command, check=check, capture_output=True, text=True)
    return result.stdout.strip()

def container_size(image, path):
    """コンテナ内パスのバイト数を測り、取得不能なら測定欠損としてNoneを返す。"""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
         f"du -sb {path} 2>/dev/null | cut -f1"],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    # コマンド成功かつ出力が整数の場合だけ採用し、それ以外はゼロではなく未計測とする。
    return int(value) if result.returncode == 0 and value.isdigit() else None

metrics = {}
for implementation, image in images.items():
    # 各実装について、Dockerメタデータとレイヤー履歴を共通形式へまとめる。
    inspected = json.loads(run(["docker", "image", "inspect", image]))[0]
    history_lines = run([
        "docker", "history", "--no-trunc", "--format",
        "{{json .}}", image,
    ]).splitlines()
    item = {
        "image": image,
        "image_id": inspected.get("Id"),
        "total_size_bytes": inspected.get("Size"),
        "repo_digests": inspected.get("RepoDigests") or None,
        "layers": [json.loads(line) for line in history_lines if line],
        "build": None,
        "ecr": None,
        "artifacts": {},
    }
    build_path = output.parent / f"{implementation}-build.json"
    # 同じ実行IDのビルド記録がある場合だけ結合し、ない場合は未収集としてNoneを維持する。
    if build_path.is_file():
        item["build"] = json.loads(build_path.read_text(encoding="utf-8"))
    # 言語ごとに成果物の構成が異なるため、実装一覧で定義したパスごとに容量を測る。
    item["artifacts"] = {
        name: container_size(image, path)
        for name, path in implementations[implementation]["artifact_paths"].items()
    }

    repository = repositories[implementation]
    # ECRリポジトリURLが設定されている場合だけ、登録済みdigestと圧縮サイズを追加する。
    if repository:
        repository_name = repository.split("/", 1)[-1]
        response = json.loads(run([
            "aws", "ecr", "describe-images", "--region", region,
            "--repository-name", repository_name, "--image-ids", f"imageTag={tag}",
            "--output", "json",
        ]))
        detail = response.get("imageDetails", [{}])[0]
        item["ecr"] = {
            "repository": repository,
            "image_digest": detail.get("imageDigest"),
            "image_size_bytes": detail.get("imageSizeInBytes"),
            "pushed_at": detail.get("imagePushedAt"),
        }
    metrics[implementation] = item

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(output)
PY
