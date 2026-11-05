"""計測した応答が、公開API契約（spec/openapi.yaml）の成功応答の形をしているかを確かめる。

HTTP状態が200でも、本文が契約どおりでなければ処理は成功していない（例: 中継層が返す
エラー本文、別の版のAPI）。失敗の速さを成功の速さとして比べないよう、計測区間の外で
本文の主要な項目を確かめる。完全なスキーマ検証は契約テストの責務とし、ここでは
計測の採否に必要な範囲だけを見る。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _health(document: dict[str, Any]) -> str | None:
    # 正本では status は "ok" 固定のため、それ以外は稼働確認の成功とみなさない。
    if document.get("status") != "ok":
        return "status が ok ではありません"
    return None


def _transform(document: dict[str, Any]) -> str | None:
    records = document.get("records")
    # 変換結果の配列と件数がそろい、件数が配列の長さと一致する場合だけ成功とする。
    if not isinstance(records, list) or not records:
        return "records が空でない配列ではありません"
    if not _is_int(document.get("count")) or document["count"] != len(records):
        return "count が records の件数と一致しません"
    return None


def _checksum(document: dict[str, Any]) -> str | None:
    # 計算・集計の結果はSHA-256の16進64文字で返す契約のため、形が違えば結果ではない。
    if not isinstance(document.get("checksum"), str) or not CHECKSUM_PATTERN.fullmatch(
        document["checksum"]
    ):
        return "checksum が64桁の16進数ではありません"
    return None


def _compute(document: dict[str, Any]) -> str | None:
    # 処理件数と反復回数が整数でなければ、計算を実行した結果とはみなさない。
    if not _is_int(document.get("processed")) or not _is_int(document.get("iterations")):
        return "processed または iterations が整数ではありません"
    return _checksum(document)


def _item(document: dict[str, Any]) -> str | None:
    item = document.get("item")
    # 取得したItemのID・値・payloadがそろわなければ、読み取りに成功した結果ではない。
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not _is_int(item.get("value"))
        or not isinstance(item.get("payload"), str)
    ):
        return "item に id・value・payload がそろっていません"
    return None


def _aggregate(document: dict[str, Any]) -> str | None:
    # 件数と合計値が整数でなければ、集計を実行した結果とはみなさない。
    if not _is_int(document.get("count")) or not _is_int(document.get("total_value")):
        return "count または total_value が整数ではありません"
    return _checksum(document)


# 経路の先頭部分から、成功応答の確認方法を選ぶ。/items/{id} はIDを含むため前方一致で扱う。
ROUTE_CHECKS: tuple[tuple[str, Callable[[dict[str, Any]], str | None]], ...] = (
    ("/health", _health),
    ("/transform", _transform),
    ("/compute", _compute),
    ("/items/", _item),
    ("/aggregate", _aggregate),
)


def _check_for(path: str) -> Callable[[dict[str, Any]], str | None] | None:
    for prefix, check in ROUTE_CHECKS:
        # 完全一致、または /items/ のような前方一致の経路だけを、その確認方法に対応付ける。
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return check
    return None


def validate_success_response(path: str, status_code: int, content: bytes) -> str | None:
    """成功応答として扱えるなら None、扱えなければ理由を返す。

    正本の成功応答はすべて200のため、それ以外の状態は失敗とする。
    契約にない経路は、JSONオブジェクトでありエラー本文でないことだけを確かめる。
    """
    # 正本の成功応答はすべて200であり、3xxや他の2xxは想定外の応答として失敗にする。
    if status_code != 200:
        return f"HTTP状態 {status_code}"
    try:
        document = json.loads(content)
    # 本文がJSONでなければ、APIの処理結果ではない（中継層のHTMLなど）。
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "応答本文がJSONではありません"
    # 正本の成功応答はすべてオブジェクトのため、配列や値だけの本文は結果とみなさない。
    if not isinstance(document, dict):
        return "応答本文がJSONオブジェクトではありません"
    # エラー本文（error キー）を200で返す応答は、処理に失敗した結果である。
    if "error" in document:
        return "応答本文がエラーを示しています"
    check = _check_for(path)
    # 契約に確認方法がある経路だけ、主要な項目まで確かめる。
    if check is None:
        return None
    reason = check(document)
    return f"応答本文が契約と一致しません（{reason}）" if reason else None
