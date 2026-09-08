from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkloadError(ValueError):
    """Raised when a workload configuration is invalid."""


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


def _positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkloadError(f"{field} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise WorkloadError(f"{field} must be >= {minimum}")
    return value


def _payload_path(config_path: Path, configured: str) -> Path:
    candidate = Path(configured)
    if candidate.is_absolute():
        return candidate
    paths = (Path.cwd() / candidate, config_path.parent / candidate)
    return next((path for path in paths if path.is_file()), paths[0])


def load_workload(config_path: Path, name: str) -> Workload:
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkloadError(f"workload file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise WorkloadError(f"invalid workload YAML: {exc}") from exc

    if not isinstance(document, dict) or document.get("version") != 1:
        raise WorkloadError("workload document version must be 1")
    definitions = document.get("workloads")
    if not isinstance(definitions, dict) or name not in definitions:
        raise WorkloadError(f"workload not found: {name}")
    raw = definitions[name]
    if not isinstance(raw, dict):
        raise WorkloadError(f"workload {name} must be a mapping")

    method = str(raw.get("method", "")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise WorkloadError(f"unsupported method for {name}: {method}")
    route = raw.get("path")
    if not isinstance(route, str) or not route.startswith("/"):
        raise WorkloadError(f"path for {name} must start with '/'")

    body = raw.get("body")
    payload_file = raw.get("payload_file")
    if body is not None and payload_file is not None:
        raise WorkloadError(f"{name} cannot define both body and payload_file")
    if payload_file is not None:
        if not isinstance(payload_file, str):
            raise WorkloadError(f"payload_file for {name} must be a string")
        path = _payload_path(config_path, payload_file)
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkloadError(f"payload file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise WorkloadError(f"invalid payload JSON: {path}: {exc}") from exc

    return Workload(
        name=name,
        method=method,
        path=route,
        warmup=_positive_int(raw.get("warmup", 0), "warmup", allow_zero=True),
        iterations=_positive_int(raw.get("iterations"), "iterations"),
        client_concurrency=_positive_int(raw.get("client_concurrency", 1), "client_concurrency"),
        body=body,
        profile=raw.get("profile") or (body.get("profile") if isinstance(body, dict) else None),
    )
