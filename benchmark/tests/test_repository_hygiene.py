"""公開リポジトリの追跡ファイルに、個人環境の情報が入っていないことを検証する。"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 利用者ごとのホームディレクトリの絶対パス（例: macOS の /Users/名前、Linux の /home/名前）。
# ユーザー名がそのまま公開されるため、追跡ファイルに書かない。
HOME_DIRECTORY_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")


def tracked_files():
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [ROOT / name for name in output.decode().split("\0") if name]


def test_editor_workspace_settings_are_not_tracked():
    workspaces = [path.name for path in tracked_files() if path.suffix == ".code-workspace"]

    # ワークスペース設定は個人環境の絶対パスを含むため、各自のローカルで管理する（WORK-102）。
    assert workspaces == []


def test_tracked_files_do_not_contain_home_directory_paths():
    offending = []
    for path in tracked_files():
        # git rm --cached の直後など、追跡情報だけが残り実体がないものは検査できないため飛ばす。
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 画像などのバイナリは文字列として読めないため、検査の対象外とする。
            continue
        if HOME_DIRECTORY_PATH.search(text):
            offending.append(str(path.relative_to(ROOT)))

    # ユーザー名を含む絶対パスが見つかった場合は、相対パスか環境変数に置き換える。
    assert offending == []
