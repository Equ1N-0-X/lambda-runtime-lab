from __future__ import annotations

import base64
import copy
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class ContractCase:
    name: str
    method: str
    path: str
    request_body: JsonValue
    expected_status: int
    expected_body: JsonValue
    ignored_response_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: JsonValue


def load_cases(case_directory: Path) -> list[ContractCase]:
    files = sorted(case_directory.glob("*.json"))
    if not files:
        raise AssertionError(f"no canonical test cases found in {case_directory}")

    cases: list[ContractCase] = []
    for file_path in files:
        with file_path.open(encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, dict):
            raise ValueError(f"{file_path}: test case must be a JSON object")
        file_cases = raw.get("cases")
        if file_cases is None:
            cases.append(_parse_case(file_path, raw))
            continue
        if not isinstance(file_cases, list) or not file_cases:
            raise ValueError(f"{file_path}: cases must be a non-empty array")
        for index, case in enumerate(file_cases):
            if not isinstance(case, dict):
                raise ValueError(f"{file_path}: cases[{index}] must be an object")
            cases.append(_parse_case(file_path, case))
    return cases


def load_fixture_items(case_directory: Path) -> list[dict[str, JsonValue]]:
    items_by_id: dict[str, dict[str, JsonValue]] = {}
    for file_path in sorted(case_directory.glob("*.json")):
        with file_path.open(encoding="utf-8") as file:
            raw = json.load(file)
        fixtures = raw.get("fixtures", {}) if isinstance(raw, dict) else {}
        if not isinstance(fixtures, dict):
            raise ValueError(f"{file_path}: fixtures must be an object")
        items = fixtures.get("items", [])
        if not isinstance(items, list):
            raise ValueError(f"{file_path}: fixtures.items must be an array")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError(f"{file_path}: every fixture item requires a string id")
            item_id = item["id"]
            previous = items_by_id.get(item_id)
            if previous is not None and previous != item:
                raise ValueError(f"{file_path}: conflicting fixture for {item_id}")
            items_by_id[item_id] = item
    return [items_by_id[item_id] for item_id in sorted(items_by_id)]


def invoke(base_url: str, case: ContractCase, transport: str) -> ApiResponse:
    if transport == "direct":
        return _invoke_direct(base_url, case)
    if transport == "lambda-rie":
        return _invoke_lambda_rie(base_url, case)
    raise ValueError("CONTRACT_TRANSPORT must be 'direct' or 'lambda-rie'")


def normalized_json(value: JsonValue, ignored_fields: tuple[str, ...]) -> str:
    normalized = copy.deepcopy(value)
    for pointer in ignored_fields:
        _remove_json_pointer(normalized, pointer)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def assert_case_matches(
    case: ContractCase,
    python_response: ApiResponse,
    rust_response: ApiResponse,
) -> None:
    assert python_response.status == case.expected_status, (
        f"{case.name}: Python status {python_response.status}, expected {case.expected_status}"
    )
    assert rust_response.status == case.expected_status, (
        f"{case.name}: Rust status {rust_response.status}, expected {case.expected_status}"
    )

    expected = normalized_json(case.expected_body, case.ignored_response_fields)
    python_body = normalized_json(python_response.body, case.ignored_response_fields)
    rust_body = normalized_json(rust_response.body, case.ignored_response_fields)

    assert python_body == expected, (
        f"{case.name}: Python response does not match the canonical response\n"
        f"expected: {expected}\nactual:   {python_body}"
    )
    assert rust_body == expected, (
        f"{case.name}: Rust response does not match the canonical response\n"
        f"expected: {expected}\nactual:   {rust_body}"
    )
    assert python_body == rust_body, (
        f"{case.name}: Python and Rust response bodies differ\n"
        f"python: {python_body}\nrust:   {rust_body}"
    )

    if case.expected_status >= 400:
        _assert_error_schema(case.name, python_response.body, "Python")
        _assert_error_schema(case.name, rust_response.body, "Rust")


def configured_api_urls() -> tuple[str, str]:
    python_url = os.getenv("PYTHON_API_URL", "").strip()
    rust_url = os.getenv("RUST_API_URL", "").strip()
    missing = [
        name
        for name, value in (
            ("PYTHON_API_URL", python_url),
            ("RUST_API_URL", rust_url),
        )
        if not value
    ]
    if missing:
        raise AssertionError(f"required environment variable(s) not set: {', '.join(missing)}")
    return python_url, rust_url


def _parse_case(file_path: Path, raw: dict[str, Any]) -> ContractCase:
    request = raw.get("request", raw)
    if not isinstance(request, dict):
        raise ValueError(f"{file_path}: request must be an object")

    expected = raw.get("expected_response", raw.get("expected", raw.get("response")))
    if not isinstance(expected, dict):
        raise ValueError(f"{file_path}: expected_response (or expected/response) must be an object")

    method = request.get("method")
    path = request.get("path")
    if not isinstance(method, str) or not method:
        raise ValueError(f"{file_path}: request.method must be a non-empty string")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{file_path}: request.path must start with '/'")

    status = expected.get("status", expected.get("status_code"))
    if not isinstance(status, int):
        raise ValueError(f"{file_path}: expected response status must be an integer")
    if "body" in expected:
        expected_body = expected["body"]
    elif "json" in expected:
        expected_body = expected["json"]
    else:
        raise ValueError(f"{file_path}: expected response body is required")

    request_body = request.get("body", request.get("json"))
    ignored = raw.get(
        "ignored_response_fields",
        raw.get("ignore_response_fields", raw.get("nondeterministic_fields", [])),
    )
    if not isinstance(ignored, list) or not all(isinstance(item, str) for item in ignored):
        raise ValueError(f"{file_path}: ignored response fields must be a list of strings")

    return ContractCase(
        name=str(raw.get("name", file_path.stem)),
        method=method.upper(),
        path=path,
        request_body=request_body,
        expected_status=status,
        expected_body=expected_body,
        ignored_response_fields=tuple(ignored),
    )


def _invoke_direct(base_url: str, case: ContractCase) -> ApiResponse:
    url = f"{base_url.rstrip('/')}/{case.path.lstrip('/')}"
    return _send_json(
        url=url,
        method=case.method,
        body=case.request_body,
        headers=_request_headers(case),
    )


def _invoke_lambda_rie(base_url: str, case: ContractCase) -> ApiResponse:
    invoke_path = "/2015-03-31/functions/function/invocations"
    url = base_url.rstrip("/")
    if not url.endswith(invoke_path):
        url += invoke_path

    event = _api_gateway_event(case)
    runtime_response = _send_json(
        url=url,
        method="POST",
        body=event,
        headers={"Content-Type": "application/json"},
    )
    if runtime_response.status != 200 or not isinstance(runtime_response.body, dict):
        raise AssertionError(
            f"{case.name}: Lambda Runtime Interface Emulator returned "
            f"{runtime_response.status}: {runtime_response.body!r}"
        )

    status = runtime_response.body.get("statusCode")
    encoded_body = runtime_response.body.get("body")
    if not isinstance(status, int) or not isinstance(encoded_body, str):
        raise AssertionError(f"{case.name}: invalid Lambda proxy response")
    if runtime_response.body.get("isBase64Encoded"):
        encoded_body = base64.b64decode(encoded_body).decode("utf-8")
    return ApiResponse(status=status, body=json.loads(encoded_body))


def _send_json(
    *,
    url: str,
    method: str,
    body: JsonValue,
    headers: dict[str, str],
) -> ApiResponse:
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url=url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URLs are test inputs.
            status = response.status
            payload = response.read()
    except HTTPError as error:
        status = error.code
        payload = error.read()
    except OSError as error:
        raise AssertionError(f"request to {url} failed: {error}") from error

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise AssertionError(f"{url} returned a non-JSON response: {payload!r}") from error
    return ApiResponse(status=status, body=parsed)


def _request_headers(case: ContractCase) -> dict[str, str]:
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"lambda-runtime-lab/{case.name}")
    headers = {
        "Accept": "application/json",
        "X-Benchmark-Run-Id": str(run_id),
    }
    if case.request_body is not None:
        headers["Content-Type"] = "application/json"
    return headers


def _api_gateway_event(case: ContractCase) -> dict[str, JsonValue]:
    split_path = urlsplit(case.path)
    headers = {key.lower(): value for key, value in _request_headers(case).items()}
    body = None
    if case.request_body is not None:
        body = json.dumps(case.request_body, ensure_ascii=False, separators=(",", ":"))
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": split_path.path,
        "rawQueryString": split_path.query,
        "headers": headers,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "local",
            "domainName": "localhost",
            "domainPrefix": "local",
            "http": {
                "method": case.method,
                "path": split_path.path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "lambda-runtime-lab-contract-test",
            },
            "requestId": str(uuid.uuid5(uuid.NAMESPACE_URL, case.name)),
            "routeKey": "$default",
            "stage": "$default",
            "time": "01/Jan/2026:00:00:00 +0000",
            "timeEpoch": 1767225600000,
        },
        "body": body,
        "isBase64Encoded": False,
    }


def _remove_json_pointer(value: JsonValue, pointer: str) -> None:
    if not pointer.startswith("/") or pointer == "/":
        raise ValueError(f"ignored field must be a non-root JSON Pointer: {pointer!r}")

    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    current: JsonValue = value
    for token in tokens[:-1]:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return

    final = tokens[-1]
    if isinstance(current, dict):
        current.pop(final, None)
    elif isinstance(current, list) and final.isdigit() and int(final) < len(current):
        current.pop(int(final))


def _assert_error_schema(name: str, body: JsonValue, implementation: str) -> None:
    assert isinstance(body, dict) and set(body) == {"error"}, (
        f"{name}: {implementation} error response must contain only the 'error' field"
    )
    error = body["error"]
    assert isinstance(error, dict), f"{name}: {implementation} error must be an object"
    assert isinstance(error.get("code"), str) and error["code"], (
        f"{name}: {implementation} error.code must be a non-empty string"
    )
    assert isinstance(error.get("message"), str) and error["message"], (
        f"{name}: {implementation} error.message must be a non-empty string"
    )
    if "details" in error:
        assert isinstance(error["details"], list), (
            f"{name}: {implementation} error.details must be an array"
        )
