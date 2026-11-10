"""YAMLで定義したワークロードを検証し、実行可能な不変設定へ変換する。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
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
    # 毎回の要求の前に新しい実行環境を起こし、すべての要求をコールドスタートとして計測するか。
    cold_start: bool = False

    def supported(self, available: tuple[str, ...]) -> tuple[str, ...]:
        """実装一覧の表示順のまま、このワークロードに対応する言語だけを返す。"""
        if self.implementations is None:
            return available
        return tuple(key for key in available if key in self.implementations)


# 公開API契約（spec/openapi.yaml の x-supported-values）で処理方式として受け付けるprofile。
KNOWN_PROFILES = ("sequential", "concurrent")


def apply_profile(workload: Workload, profile: str | None) -> Workload:
    """送信本文のprofileを指定値へ置き換え、記録するprofileを本文と一致させて返す。

    profileは本文の項目としてAPIへ送られる。記録だけを変えると、記録と異なる処理方式で
    計測したrunになるため、本文と記録を必ず同じ値にする。
    """
    body = workload.body
    body_profile = body.get("profile") if isinstance(body, dict) else None
    # 上書きがなければ、本文のprofileをそのまま記録する（本文にないなら記録もしない）。
    if profile is None:
        return replace(workload, profile=body_profile)
    # 本文がprofileを持たないワークロードでは、指定しても送られないため誤りとして止める。
    if body_profile is None:
        raise WorkloadError(
            f"ワークロード {workload.name} の本文にはprofileがないため、profileを指定できません"
        )
    # 契約にない処理方式を送ると、全件がUNSUPPORTED_PROFILEになり比較にならない。
    if profile not in KNOWN_PROFILES:
        raise WorkloadError(
            f"profile は {' または '.join(KNOWN_PROFILES)} を指定してください: {profile}"
        )
    return replace(workload, body={**body, "profile": profile}, profile=profile)


# コールドスタート計測の反復回数の上限。1回ごとに関数の設定を更新して待つため時間がかかる。
MAX_COLD_START_ITERATIONS = 100


def check_cold_start(workload: Workload) -> None:
    """コールドスタート計測の条件（ウォームアップなし・同時要求1・回数の上限）を確かめる。

    ウォームアップや同時要求があると、新しく起こした実行環境が計測前に使われ、
    計測した要求がコールドスタートにならない。
    """
    # 通常の計測には、この制約を課さない。
    if not workload.cold_start:
        return
    if workload.warmup != 0 or workload.client_concurrency != 1:
        raise WorkloadError(
            f"ワークロード {workload.name} はコールドスタートの計測のため、"
            "warmupは0、client_concurrencyは1で指定してください"
        )
    if workload.iterations > MAX_COLD_START_ITERATIONS:
        raise WorkloadError(
            f"ワークロード {workload.name} の反復回数は {MAX_COLD_START_ITERATIONS} 回以下で"
            "指定してください（1回ごとに関数の設定を更新するため）"
        )


def experiment_id(workload: Workload) -> str:
    """処理の内容（方式・経路・本文）と同時要求数から、同じ実験かを照合する識別子を返す。

    反復回数とウォームアップは標本の数を変えるだけで1リクエストの処理は変えないため含めない。
    同時要求数は同じ実行環境での競合や再利用に影響するため含める。
    """
    conditions: dict[str, Any] = {
        "method": workload.method,
        "path": workload.path,
        "body": workload.body,
        "client_concurrency": workload.client_concurrency,
    }
    # 同じ処理でも、毎回コールドスタートさせた計測は別の実験として扱う。
    # 通常の計測では項目を加えず、これまでの識別子を変えない。
    if workload.cold_start:
        conditions["cold_start"] = True
    document = json.dumps(
        conditions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(document.encode()).hexdigest()


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

    configured_profile = raw.get("profile")
    body_profile = body.get("profile") if isinstance(body, dict) else None
    # 定義のprofileと本文のprofileが食い違えば、どちらで計測したと記録すべきか決められない。
    if configured_profile is not None and configured_profile != body_profile:
        raise WorkloadError(
            f"ワークロード {name} のprofile（{configured_profile}）が本文のprofile"
            f"（{body_profile}）と一致しません"
        )

    mode = raw.get("mode", "standard")
    # 計測方式は、通常（standard）と毎回コールドスタートさせる方式（cold-start）だけを受け付ける。
    if mode not in ("standard", "cold-start"):
        raise WorkloadError(
            f"ワークロード {name} のmodeは standard または cold-start で指定してください: {mode}"
        )

    workload = Workload(
        name=name,
        method=method,
        path=route,
        warmup=_positive_int(raw.get("warmup", 0), "warmup", allow_zero=True),
        iterations=_positive_int(raw.get("iterations"), "iterations"),
        client_concurrency=_positive_int(raw.get("client_concurrency", 1), "client_concurrency"),
        body=body,
        profile=body_profile,
        description=raw.get("description"),
        implementations=_implementations(raw.get("implementations"), name),
        cold_start=mode == "cold-start",
    )
    check_cold_start(workload)
    return workload
