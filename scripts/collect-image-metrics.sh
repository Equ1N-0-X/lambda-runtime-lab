#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${RUN_ID:-}"
TAG="${TAG:-benchmark}"
REGION="${AWS_REGION:-ap-northeast-1}"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ -z "${RUN_ID}" ]]; then
  echo "RUN_ID is required" >&2
  exit 2
fi

python3 - "${ROOT}" "${RUN_ID}" "${TAG}" "${REGION}" "${DRY_RUN}" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

root, run_id, tag, region, dry_run = sys.argv[1:]
output = Path(root) / "results" / "raw" / run_id / "image-metrics.json"
images = {
    "python": f"lambda-runtime-lab-python:{tag}",
    "rust": f"lambda-runtime-lab-rust:{tag}",
}
repositories = {
    "python": os.getenv("PYTHON_ECR_REPOSITORY_URL"),
    "rust": os.getenv("RUST_ECR_REPOSITORY_URL"),
}

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
    result = subprocess.run(command, check=check, capture_output=True, text=True)
    return result.stdout.strip()

def container_size(image, path):
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
         f"du -sb {path} 2>/dev/null | cut -f1"],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None

metrics = {}
for implementation, image in images.items():
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
    if build_path.is_file():
        item["build"] = json.loads(build_path.read_text(encoding="utf-8"))
    if implementation == "python":
        item["artifacts"] = {
            "runtime_size_bytes": container_size(image, "/var/lang"),
            "site_packages_size_bytes": container_size(
                image, "/opt/python"
            ),
            "application_source_size_bytes": container_size(image, "/var/task"),
        }
    else:
        item["artifacts"] = {
            "release_binary_size_bytes": container_size(image, "/var/runtime/bootstrap")
        }

    repository = repositories[implementation]
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
