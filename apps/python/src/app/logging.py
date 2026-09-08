import json
import logging
import sys
from typing import Any

from fastapi import Request


def _create_logger() -> logging.Logger:
    logger = logging.getLogger("lambdalith")
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
    context: Any = request.scope.get("aws.context")
    return getattr(context, "aws_request_id", None)


def log_request_completed(request: Request, status_code: int) -> None:
    route = request.scope.get("route")
    fields = {
        "benchmark_run_id": request.headers.get("x-benchmark-run-id"),
        "implementation": "python",
        "lambda_request_id": lambda_request_id(request),
        "level": "INFO",
        "message": "request completed",
        "profile": getattr(request.state, "profile", None),
        "route": getattr(route, "path", request.url.path),
        "status_code": status_code,
    }
    LOGGER.info(json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def log_internal_error(request: Request, error: Exception) -> None:
    fields = {
        "benchmark_run_id": request.headers.get("x-benchmark-run-id"),
        "error_type": type(error).__name__,
        "implementation": "python",
        "lambda_request_id": lambda_request_id(request),
        "level": "ERROR",
        "message": "request failed",
        "profile": getattr(request.state, "profile", None),
        "route": request.url.path,
        "status_code": 500,
    }
    LOGGER.error(json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
