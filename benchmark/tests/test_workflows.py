"""CIのworkflowが、AWSを使わずに一度だけ、lockの食い違いを検出して動くことを検証する。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_no_workflow_operates_aws():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))

    # CIは挙動の確認に限り、AWSの環境を作る手動計測のworkflowは置かない（D02で廃止）。
    assert [path.name for path in workflows] == ["ci.yml"]
    text = workflows[0].read_text()
    assert "configure-aws-credentials" not in text and "id-token" not in text


def test_ci_checks_lock_consistency_instead_of_only_freezing():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    commands = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )

    # --frozen は lock の食い違いを検出しないため、全ての uv 呼び出しを --locked と同じにする。
    assert workflow["env"]["UV_LOCKED"] == "1"
    assert "--frozen" not in commands
    contract = (ROOT / "scripts/run-contract-test.sh").read_text()
    assert contract.count("uv run --locked") == 2


def test_ci_runs_once_per_pull_request_update_and_keeps_main_checks():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAMLはキー on を真偽値 True として読む。
    triggers = workflow[True]

    # 作業ブランチへの push とプルリクエストの二重実行を避け、main への push は検証する。
    assert triggers["push"] == {"branches": ["main"]}
    assert "pull_request" in triggers
    # PRを作らない作業ブランチは手動で検証できる。
    assert "workflow_dispatch" in triggers


def test_locked_mode_fails_without_rewriting_a_stale_lock(tmp_path):
    import shutil
    import subprocess

    uv = shutil.which("uv")
    assert uv is not None
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
        "dependencies = []\n",
        encoding="utf-8",
    )
    subprocess.run([uv, "lock", "--offline", "--project", str(tmp_path)], check=True)
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    # 依存の定義だけを変え、lock を更新しないまま検証する。
    (tmp_path / "pyproject.toml").write_text(
        (tmp_path / "pyproject.toml").read_text().replace('version = "0.1.0"', 'version = "0.2.0"'),
        encoding="utf-8",
    )

    result = subprocess.run(
        [uv, "lock", "--check", "--offline", "--project", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (tmp_path / "uv.lock").read_text(encoding="utf-8") == lock
