"""比較対象の言語実装の一覧（spec/implementations.yaml）を読み、検証済みの定義として返す。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = ROOT / "spec/implementations.yaml"

# 環境変数名・AWSリソース名・結果ファイルに埋め込むため、小文字英数字だけを許可する。
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,19}$")


class ImplementationError(ValueError):
    """実装一覧の内容が正しくないことを表す。"""


@dataclass(frozen=True)
class Implementation:
    """比較対象の一つの言語実装。"""

    key: str
    label: str
    app_dir: str
    # イメージ指標として容量を測るコンテナ内のパス（指標名からパスへの対応表）。
    artifact_paths: dict[str, str] = field(default_factory=dict, compare=False, hash=False)
    # app_dirのうち、イメージに入らないテストのパス（app_dirからの相対パス）。
    test_paths: tuple[str, ...] = field(default=(), compare=False, hash=False)

    @property
    def env_prefix(self) -> str:
        """接続先などを環境変数で上書きするときの接頭辞（例: PYTHON）。"""
        return self.key.upper()

    @property
    def image_source_pathspecs(self) -> tuple[str, ...]:
        """イメージの元になるファイルを表すGitのpathspec。テストだけの変更は版のずれとみなさない。"""
        return (
            self.app_dir,
            *(f":(exclude){self.app_dir}/{path}" for path in self.test_paths),
        )


def load_implementations(path: Path = DEFAULT_REGISTRY) -> tuple[Implementation, ...]:
    """実装一覧を表示順のまま読み込む。先頭の実装が差分の基準になる。"""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = document.get("implementations")
    # 一覧が空または対応表でなければ、比較対象を決められないため読込を止める。
    if not isinstance(entries, dict) or not entries:
        raise ImplementationError(f"{path}: implementations に一つ以上の実装を定義してください")

    implementations = []
    for key, definition in entries.items():
        # キーの形式が外れていれば、環境変数名やリソース名を安全に作れない。
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise ImplementationError(
                f"{path}: 実装のキー {key!r} は"
                "小文字英字で始まる小文字英数字（20文字以内）にしてください"
            )
        # 表示名とディレクトリは画面・ビルドで必須のため、欠けていれば定義の誤りとする。
        if (
            not isinstance(definition, dict)
            or not definition.get("label")
            or not definition.get("app_dir")
        ):
            raise ImplementationError(f"{path}: {key} には label と app_dir が必要です")
        artifact_paths = definition.get("artifact_paths") or {}
        # 容量を測るパスは指標名とパスの対応表でなければ、イメージ指標を集められない。
        if not isinstance(artifact_paths, dict):
            raise ImplementationError(f"{path}: {key} の artifact_paths は対応表で指定してください")
        test_paths = definition.get("test_paths") or []
        # テストのパスがapp_dirの外を指すと、照合から外す範囲がアプリの外へ広がってしまう。
        if not isinstance(test_paths, list) or not all(
            isinstance(item, str)
            and item
            and not item.startswith("/")
            and ".." not in item.split("/")
            for item in test_paths
        ):
            raise ImplementationError(
                f"{path}: {key} の test_paths は app_dir からの相対パスの一覧で指定してください"
            )
        implementations.append(
            Implementation(
                key=key,
                label=str(definition["label"]),
                app_dir=str(definition["app_dir"]),
                artifact_paths={str(name): str(value) for name, value in artifact_paths.items()},
                test_paths=tuple(test_paths),
            )
        )
    return tuple(implementations)


def resolve_targets(target: str, available: tuple[str, ...]) -> tuple[str, ...]:
    """計測対象の指定を、一覧の表示順に並べた実装キーへ正規化する。

    all は一覧の全実装、カンマ区切りはその実装だけを表す。
    """
    # all（旧来のbothも同義）なら、一覧にある全実装を対象にする。
    if target in ("all", "both"):
        return available
    requested = [name.strip() for name in target.split(",") if name.strip()]
    unknown = [name for name in requested if name not in available]
    # 一覧にない実装名が含まれていれば、誤記の可能性が高いため計測を始めない。
    if not requested or unknown:
        raise ValueError(
            f"target には all または {', '.join(available)} をカンマ区切りで指定してください"
            + (f"（不明: {', '.join(unknown)}）" if unknown else "")
        )
    return tuple(name for name in available if name in requested)
