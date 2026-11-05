"""環境の切替時に、読み込んだファイルにない接続先の上書きが残っていれば警告することを検証する。"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _switch(tmp_path):
    shutil.copy(ROOT / ".env.export.sh", tmp_path)
    (tmp_path / ".env").write_text("AWS_REGION=ap-northeast-1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("PYTHON_API_URL=http://127.0.0.1:3000\n", encoding="utf-8")
    script = (
        "source ./.env.export.sh --local; echo '--- switch' >&2; "
        'source ./.env.export.sh; echo "kept=${PYTHON_API_URL}"'
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True
    )


def test_switching_environment_warns_about_leftover_endpoint(tmp_path):
    result = _switch(tmp_path)

    before, after = result.stderr.split("--- switch")
    # 上書きを定義したファイルを読んだときは警告せず、別の環境へ切り替えたときだけ警告する。
    assert "警告" not in before
    assert "unset PYTHON_API_URL" in after
    # 外から意図して渡した可能性があるため、値は消さずに残す。
    assert "kept=http://127.0.0.1:3000" in result.stdout
