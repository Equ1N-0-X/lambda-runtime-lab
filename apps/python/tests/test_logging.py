"""構造化ログの日本語メッセージ、レベル、相関フィールドを検証する。"""

import json
import logging
from typing import Any

from fastapi import Request

from app import logging as app_logging


def _request() -> Request:
    """ログ検証に必要なヘッダー・経路・AWSコンテキストを持つ最小リクエストを作る。"""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [
                (b"x-benchmark-run-id", b"run-1"),
                (b"x-benchmark-request-index", b"7"),
            ],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _capture(monkeypatch: Any) -> list[tuple[int, dict[str, Any]]]:
    """ロガーへ渡されたレベルとJSON本文を、標準出力へ出さず記録する。"""
    captured: list[tuple[int, dict[str, Any]]] = []

    def record(level: int, message: str) -> None:
        captured.append((level, json.loads(message)))

    monkeypatch.setattr(app_logging.LOGGER, "log", record)
    return captured


def test_info_log_uses_japanese_message_and_correlation_fields(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)

    app_logging.log_request_completed(_request(), 200)

    assert captured == [
        (
            logging.INFO,
            {
                "benchmark_request_index": 7,
                "benchmark_run_id": "run-1",
                "implementation": "python",
                "lambda_request_id": None,
                "level": "INFO",
                "message": "リクエスト処理が完了しました",
                "profile": None,
                "route": "/health",
                "status_code": 200,
            },
        )
    ]


def test_dependency_error_is_logged_as_warning(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)

    app_logging.log_dependency_error(_request())

    level, fields = captured[0]
    assert level == logging.WARNING
    assert fields["level"] == "WARN"
    assert fields["message"] == "外部データストアへのアクセスに失敗しました"
    assert fields["status_code"] == 502


def test_unexpected_error_is_logged_as_error_without_secret_values(monkeypatch: Any) -> None:
    captured = _capture(monkeypatch)

    app_logging.log_internal_error(_request(), RuntimeError("秘密の詳細"))

    level, fields = captured[0]
    assert level == logging.ERROR
    assert fields["level"] == "ERROR"
    assert fields["message"] == "リクエスト処理中に予期しないエラーが発生しました"
    assert fields["error_type"] == "RuntimeError"
    assert "秘密の詳細" not in json.dumps(fields, ensure_ascii=False)
