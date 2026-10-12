"""Lambda の構造化ログを一元化する。

各ログへベンチマーク実行ID、計測リクエスト番号、LambdaリクエストID、HTTP経路を付与し、
CloudWatchから性能測定結果とアプリケーションログを相関できるようにする。リクエスト本文や
認証情報は記録しない。
"""

import json
import logging
import sys
from typing import Any

from fastapi import Request


def _create_logger() -> logging.Logger:
    """CloudWatch に JSON だけを出力するアプリケーションロガーを作成する。"""
    logger = logging.getLogger("lambdalith")

    # 同じ実行環境でモジュールが再読み込みされ、既にハンドラーがある場合は、
    # 二重登録による同一ログの重複出力を防ぐため既存ロガーを再利用する。
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


LOGGER = _create_logger()


def lambda_request_id(request: Request) -> str | None:
    """AWS Lambda 実行コンテキストから、CloudWatch照合に使うリクエストIDを取得する。"""
    context: Any = request.scope.get("aws.context")
    return getattr(context, "aws_request_id", None)


def benchmark_request_index(request: Request) -> int | None:
    """ランナーが付与した番号だけをログ相関用の整数として受け付ける。"""
    value = request.headers.get("x-benchmark-request-index")

    # ヘッダーがない通常リクエストや整数でない値は、誤相関を避けるため番号なしとして扱う。
    if value is None:
        return None
    try:
        index = int(value)
    except ValueError:
        return None

    # ランナーは測定を正数、ウォームアップを負数で表すため、未指定の代替値となる0は採用しない。
    return index if index != 0 else None


def _request_fields(
    request: Request, *, status_code: int, level: str, message: str
) -> dict[str, Any]:
    """ログレベルが異なっても、計測・調査に必要な共通項目を揃える。"""
    route = request.scope.get("route")
    return {
        "benchmark_request_index": benchmark_request_index(request),
        "benchmark_run_id": request.headers.get("x-benchmark-run-id"),
        "implementation": "python",
        "lambda_request_id": lambda_request_id(request),
        "level": level,
        "message": message,
        "profile": getattr(request.state, "profile", None),
        "route": getattr(route, "path", request.url.path),
        "status_code": status_code,
    }


def _write(level: int, fields: dict[str, Any]) -> None:
    """JSONのキー順を固定して出力し、言語間比較とログ解析を安定させる。"""
    LOGGER.log(level, json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def log_request_completed(request: Request, status_code: int) -> None:
    """仕様どおりに応答を返したリクエストの完了を INFO として記録する。"""
    _write(
        logging.INFO,
        _request_fields(
            request,
            status_code=status_code,
            level="INFO",
            message="リクエスト処理が完了しました",
        ),
    )


def log_dependency_error(request: Request) -> None:
    """外部データストアの障害により 502 を返す事実を WARN として記録する。"""
    _write(
        logging.WARNING,
        _request_fields(
            request,
            status_code=502,
            level="WARN",
            message="外部データストアへのアクセスに失敗しました",
        ),
    )


def log_internal_error(request: Request, error: Exception) -> None:
    """予期しない例外で 500 を返す事実を、例外種別だけとともに ERROR 記録する。"""
    fields = _request_fields(
        request,
        status_code=500,
        level="ERROR",
        message="リクエスト処理中に予期しないエラーが発生しました",
    )
    fields["error_type"] = type(error).__name__

    _write(logging.ERROR, fields)
