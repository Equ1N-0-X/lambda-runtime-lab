"""YAMLで定義したワークロードを検証し、実行可能な不変設定へ変換する。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkloadError(ValueError):
    """ワークロード定義が不足または不正な場合に通知する。"""


@dataclass(frozen=True)
class Workload:
    name: str
    method: str
    path: str
    warmup: int
    iterations: int
    client_concurrency: int
    body: Any | None = None
    profile: str | None = None
    # 画面で処理の内容を伝える一文。
    description: str | None = None
    # このワークロードを実装済みの言語（実装キー）。Noneなら実装一覧の全言語が対応している。
    implementations: tuple[str, ...] | None = None

    def supported(self, available: tuple[str, ...]) -> tuple[str, ...]:
        """実装一覧の表示順のまま、このワークロードに対応する言語だけを返す。"""
        if self.implementations is None:
            return available
        return tuple(key for key in available if key in self.implementations)


def _positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    """真偽値を整数と誤認せず、指定項目が許可した最小値以上か確認する。"""

    # boolはintの派生型だが件数指定として扱わず、整数以外と同様に設定エラーにする。
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkloadError(f"{field} は整数で指定してください")

    minimum = 0 if allow_zero else 1

    # warmupだけは0を許可し、反復回数や並行数は処理が一件以上行われる値を要求する。
    if value < minimum:
        raise WorkloadError(f"{field} は{minimum}以上で指定してください")
    return value


def _payload_path(config_path: Path, configured: str) -> Path:
    """絶対パスはそのまま使い、相対パスは作業場所と設定ファイル位置の順で解決する。"""
    candidate = Path(configured)
    # 絶対パスなら基準ディレクトリを加えず、そのファイルを直接参照する。
    if candidate.is_absolute():
        return candidate
    paths = (Path.cwd() / candidate, config_path.parent / candidate)
    return next((path for path in paths if path.is_file()), paths[0])


def _implementations(raw: object, name: str) -> tuple[str, ...] | None:
    """対応言語の指定を検証する。未指定なら全言語が対応しているものとしてNoneを返す。"""
    if raw is None:
        return None
    # 実装キーの空でない一覧でなければ、どの言語で計測できるか決められない。
    if not isinstance(raw, list) or not raw or not all(isinstance(key, str) for key in raw):
        raise WorkloadError(
            f"ワークロード {name} のimplementationsは実装キーの一覧で指定してください"
        )
    return tuple(raw)


def load_workload(config_path: Path, name: str) -> Workload:
    """対象名の定義を読み、HTTP方式・経路・本文・実行回数を検証して返す。"""
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkloadError(f"ワークロードファイルが見つかりません: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise WorkloadError(f"ワークロードYAMLが不正です: {exc}") from exc

    # ルートがオブジェクトかつversion=1の場合だけ、現在の定義形式として読み込む。
    if not isinstance(document, dict) or document.get("version") != 1:
        raise WorkloadError("ワークロード定義のversionは1である必要があります")
    definitions = document.get("workloads")
    # workloadsがオブジェクトで対象名を含む場合だけ、その定義を選択する。
    if not isinstance(definitions, dict) or name not in definitions:
        raise WorkloadError(f"ワークロードが見つかりません: {name}")
    raw = definitions[name]
    # 個別ワークロードがキーと値の組でなければ、項目を安全に読み取れないため拒否する。
    if not isinstance(raw, dict):
        raise WorkloadError(f"ワークロード {name} はマッピング形式で指定してください")

    method = str(raw.get("method", "")).upper()
    # HTTPメソッドが許可一覧にない場合は、Runnerが意図しない操作を送らないよう拒否する。
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise WorkloadError(f"ワークロード {name} のHTTPメソッドは未対応です: {method}")
    route = raw.get("path")
    # 経路が文字列かつスラッシュ開始でない場合は、API URLとの安全な結合ができないため拒否する。
    if not isinstance(route, str) or not route.startswith("/"):
        raise WorkloadError(f"ワークロード {name} のpathは '/' から始めてください")

    body = raw.get("body")
    payload_file = raw.get("payload_file")
    # 本文の直接指定とファイル指定が両方ある場合は正本が曖昧になるため拒否する。
    if body is not None and payload_file is not None:
        raise WorkloadError(f"{name} ではbodyとpayload_fileを同時に指定できません")

    # payload_fileが指定された場合だけ、文字列パスを解決してJSON本文として読み込む。
    if payload_file is not None:
        if not isinstance(payload_file, str):
            raise WorkloadError(f"{name} のpayload_fileは文字列で指定してください")
        path = _payload_path(config_path, payload_file)
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkloadError(f"payloadファイルが見つかりません: {path}") from exc
        except json.JSONDecodeError as exc:
            raise WorkloadError(f"payloadのJSONが不正です: {path}: {exc}") from exc

    return Workload(
        name=name,
        method=method,
        path=route,
        warmup=_positive_int(raw.get("warmup", 0), "warmup", allow_zero=True),
        iterations=_positive_int(raw.get("iterations"), "iterations"),
        client_concurrency=_positive_int(raw.get("client_concurrency", 1), "client_concurrency"),
        body=body,
        profile=raw.get("profile") or (body.get("profile") if isinstance(body, dict) else None),
        description=raw.get("description"),
        implementations=_implementations(raw.get("implementations"), name),
    )
