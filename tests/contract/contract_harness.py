"""共有テストケースを読み、比較対象の全言語のAPIの応答を同じ契約と照合する。"""

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

import yaml

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


@dataclass(frozen=True)
class ContractTarget:
    """契約を検証する一つの言語実装と、その接続先。"""

    key: str
    label: str
    url: str
    transport: str


def load_cases(case_directory: Path) -> list[ContractCase]:
    """正規JSONファイルを名前順に読み、単一・複数ケース形式を平坦な一覧へ変換する。"""
    files = sorted(case_directory.glob("*.json"))
    # JSONファイルが一件もなければ契約検証にならないため、空の成功扱いにせず停止する。
    if not files:
        raise AssertionError(f"正規テストケースが見つかりません: {case_directory}")

    cases: list[ContractCase] = []
    for file_path in files:
        with file_path.open(encoding="utf-8") as file:
            raw = json.load(file)
        # ファイルのルートがオブジェクトでない場合は、ケース項目を取得できないため拒否する。
        if not isinstance(raw, dict):
            raise ValueError(f"{file_path}: テストケースはJSONオブジェクトで指定してください")
        file_cases = raw.get("cases")
        # cases項目がない場合はファイル全体を一ケースとして解釈する。
        if file_cases is None:
            cases.append(_parse_case(file_path, raw))
            continue
        # cases項目がある場合は、一件以上の配列だけを複数ケース形式として許可する。
        if not isinstance(file_cases, list) or not file_cases:
            raise ValueError(f"{file_path}: casesは空でない配列で指定してください")
        for index, case in enumerate(file_cases):
            # 配列要素がオブジェクトでない場合は、要求・期待応答を取得できないため拒否する。
            if not isinstance(case, dict):
                raise ValueError(f"{file_path}: cases[{index}]はオブジェクトで指定してください")
            cases.append(_parse_case(file_path, case))
    return cases


def load_fixture_items(case_directory: Path) -> list[dict[str, JsonValue]]:
    """全ケースのfixture項目をIDで統合し、競合がない決定的な投入順で返す。"""
    items_by_id: dict[str, dict[str, JsonValue]] = {}
    for file_path in sorted(case_directory.glob("*.json")):
        with file_path.open(encoding="utf-8") as file:
            raw = json.load(file)
        fixtures = raw.get("fixtures", {}) if isinstance(raw, dict) else {}
        # fixturesがオブジェクトでない場合はitemsを安全に取得できないため拒否する。
        if not isinstance(fixtures, dict):
            raise ValueError(f"{file_path}: fixturesはオブジェクトで指定してください")
        items = fixtures.get("items", [])
        # fixtures.itemsが配列でない場合は複数項目として処理できないため拒否する。
        if not isinstance(items, list):
            raise ValueError(f"{file_path}: fixtures.itemsは配列で指定してください")
        for item in items:
            # fixtureがオブジェクトで文字列IDを持たない場合は、DynamoDBキーを作れないため拒否する。
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError(f"{file_path}: 全fixture項目に文字列idが必要です")
            item_id = item["id"]
            previous = items_by_id.get(item_id)
            # 同じIDが既出かつ内容が異なる場合は、どちらを投入するか曖昧なため競合として拒否する。
            if previous is not None and previous != item:
                raise ValueError(f"{file_path}: fixture {item_id} の内容が競合しています")
            items_by_id[item_id] = item
    return [items_by_id[item_id] for item_id in sorted(items_by_id)]


def invoke(base_url: str, case: ContractCase, transport: str) -> ApiResponse:
    """transport設定に従い、HTTP API直結またはLambda RIE経由で一ケースを呼び出す。"""

    # directならAPI経路へ直接送信し、lambda-rieならLambdaプロキシイベントに包んで送信する。
    if transport == "direct":
        return _invoke_direct(base_url, case)
    if transport == "lambda-rie":
        return _invoke_lambda_rie(base_url, case)
    raise ValueError("CONTRACT_TRANSPORTは'direct'または'lambda-rie'で指定してください")


def normalized_json(value: JsonValue, ignored_fields: tuple[str, ...]) -> str:
    """比較対象だけから、キー順と区切りが一定のJSON文字列を作る。"""
    normalized = copy.deepcopy(value)
    for pointer in ignored_fields:
        _remove_json_pointer(normalized, pointer)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def assert_case_matches(case: ContractCase, responses: dict[str, ApiResponse]) -> None:
    """全実装のHTTP状態・本文・エラー形式が正規期待値と一致することを検証する。

    responsesは表示名から応答への対応表。全実装が同じ正規期待値と一致すれば、
    実装同士の応答も互いに一致する。
    """
    expected = normalized_json(case.expected_body, case.ignored_response_fields)
    for label, response in responses.items():
        assert response.status == case.expected_status, (
            f"{case.name}: {label} status {response.status}, expected {case.expected_status}"
        )
        actual = normalized_json(response.body, case.ignored_response_fields)
        assert actual == expected, (
            f"{case.name}: {label} response does not match the canonical response\n"
            f"expected: {expected}\nactual:   {actual}"
        )
        # 期待状態が400以上の場合だけ、本文一致に加えて共通エラー構造も検査する。
        if case.expected_status >= 400:
            _assert_error_schema(case.name, response.body, label)


def configured_targets(registry_path: Path) -> list[ContractTarget]:
    """実装一覧の全言語について、接続先URLと呼び出し方式を環境変数から集める。

    URLは <KEY>_API_URL、呼び出し方式は <KEY>_CONTRACT_TRANSPORT（既定は CONTRACT_TRANSPORT、
    その既定は direct）で受け取る。
    """
    document = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    default_transport = os.getenv("CONTRACT_TRANSPORT", "direct").strip()
    targets = []
    missing = []
    for key, definition in (document.get("implementations") or {}).items():
        prefix = key.upper()
        url = os.getenv(f"{prefix}_API_URL", "").strip()
        # URLがない実装を集め、一部の言語だけを検証して成功扱いにしない。
        if not url:
            missing.append(f"{prefix}_API_URL")
            continue
        transport = os.getenv(f"{prefix}_CONTRACT_TRANSPORT", default_transport).strip()
        targets.append(ContractTarget(key, str(definition.get("label", key)), url, transport))
    # いずれかの実装のURLが空なら、不足環境変数を列挙して停止する。
    if missing:
        raise AssertionError(f"必須環境変数が設定されていません: {', '.join(missing)}")
    return targets


def _parse_case(file_path: Path, raw: dict[str, Any]) -> ContractCase:
    """互換キーを含む生定義から、検証済みの一契約ケースを組み立てる。"""
    request = raw.get("request", raw)
    # requestがオブジェクトでなければHTTP要求を構成できないため拒否する。
    if not isinstance(request, dict):
        raise ValueError(f"{file_path}: requestはオブジェクトで指定してください")

    expected = raw.get("expected_response", raw.get("expected", raw.get("response")))
    # 期待応答がどの互換キーにもなくオブジェクトでもない場合は比較できないため拒否する。
    if not isinstance(expected, dict):
        raise ValueError(
            f"{file_path}: expected_response（またはexpected/response）は"
            "オブジェクトで指定してください"
        )

    method = request.get("method")
    path = request.get("path")
    # methodが空でない文字列、pathがスラッシュ開始文字列の場合だけHTTP要求として採用する。
    if not isinstance(method, str) or not method:
        raise ValueError(f"{file_path}: request.methodには空でない文字列が必要です")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{file_path}: request.pathは'/'から始めてください")

    status = expected.get("status", expected.get("status_code"))
    # 期待HTTP状態が整数でなければ実応答と比較できないため拒否する。
    if not isinstance(status, int):
        raise ValueError(f"{file_path}: 期待応答statusは整数で指定してください")

    # bodyを優先し、互換キーjsonがあれば代用する。どちらもなければ期待本文不足として拒否する。
    if "body" in expected:
        expected_body = expected["body"]
    elif "json" in expected:
        expected_body = expected["json"]
    else:
        raise ValueError(f"{file_path}: 期待応答本文が必要です")

    request_body = request.get("body", request.get("json"))
    ignored = raw.get(
        "ignored_response_fields",
        raw.get("ignore_response_fields", raw.get("nondeterministic_fields", [])),
    )
    # 無視項目はJSON Pointer文字列の配列だけを許可し、それ以外は比較条件が不明なため拒否する。
    if not isinstance(ignored, list) or not all(isinstance(item, str) for item in ignored):
        raise ValueError(f"{file_path}: 無視する応答項目は文字列配列で指定してください")

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
    """ベースURLとケース経路を結合し、通常のHTTP要求として送信する。"""
    url = f"{base_url.rstrip('/')}/{case.path.lstrip('/')}"
    return _send_json(
        url=url,
        method=case.method,
        body=case.request_body,
        headers=_request_headers(case),
    )


def _invoke_lambda_rie(base_url: str, case: ContractCase) -> ApiResponse:
    """ケースをAPI Gateway v2イベントへ変換し、Lambda RIEのinvoke APIへ送信する。"""
    invoke_path = "/2015-03-31/functions/function/invocations"
    url = base_url.rstrip("/")
    # ベースURLがinvokeパスを含まない場合だけ既定パスを追加し、二重連結を防ぐ。
    if not url.endswith(invoke_path):
        url += invoke_path

    event = _api_gateway_event(case)
    runtime_response = _send_json(
        url=url,
        method="POST",
        body=event,
        headers={"Content-Type": "application/json"},
    )
    # RIE自体が200でなく、またはプロキシ応答がオブジェクトでない場合はLambda呼び出し失敗とする。
    if runtime_response.status != 200 or not isinstance(runtime_response.body, dict):
        raise AssertionError(
            f"{case.name}: Lambda Runtime Interface Emulator returned "
            f"{runtime_response.status}: {runtime_response.body!r}"
        )

    status = runtime_response.body.get("statusCode")
    encoded_body = runtime_response.body.get("body")
    # statusCodeが整数、bodyが文字列でない場合はAPI Gatewayプロキシ応答として不正とする。
    if not isinstance(status, int) or not isinstance(encoded_body, str):
        raise AssertionError(f"{case.name}: Lambdaプロキシ応答が不正です")

    # isBase64Encodedがtrueの場合だけ本文を復号し、通常文字列はそのままJSON解析する。
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
    """JSON要求を送信し、成功・HTTPエラーのどちらも同じApiResponseへ変換する。"""
    data = None
    # 本文がある場合だけUTF-8 JSONへ変換し、本文なし要求はdata=Noneで送信する。
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url=url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URLs are test inputs.
            status = response.status
            payload = response.read()
    # HTTPErrorは契約上比較すべき4xx/5xx応答として本文を読み、通信失敗だけテスト異常にする。
    except HTTPError as error:
        status = error.code
        payload = error.read()
    except OSError as error:
        raise AssertionError(f"{url}への要求に失敗しました: {error}") from error

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise AssertionError(f"{url}がJSON以外の応答を返しました: {payload!r}") from error
    return ApiResponse(status=status, body=parsed)


def _request_headers(case: ContractCase) -> dict[str, str]:
    """ケース名から決定的な相関IDを作り、本文がある場合だけContent-Typeを付ける。"""
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"lambda-runtime-lab/{case.name}")
    headers = {
        "Accept": "application/json",
        "X-Benchmark-Run-Id": str(run_id),
    }
    # 本文がある要求だけapplication/jsonを指定し、GETなど本文なし要求には付けない。
    if case.request_body is not None:
        headers["Content-Type"] = "application/json"
    return headers


def _api_gateway_event(case: ContractCase) -> dict[str, JsonValue]:
    """直接HTTPケースを、Lambda RIEが受け取るAPI Gateway v2イベントへ変換する。"""
    split_path = urlsplit(case.path)
    headers = {key.lower(): value for key, value in _request_headers(case).items()}
    body = None
    # ケース本文がある場合だけJSON文字列化し、ない場合はイベントbodyをnullにする。
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
    """比較から除外すると明示された非ルートJSON Pointerの値だけを削除する。"""

    # 先頭スラッシュがない、またはルートそのものを指す指定は比較全体を無効化するため拒否する。
    if not pointer.startswith("/") or pointer == "/":
        raise ValueError(f"無視項目にはルート以外のJSON Pointerを指定してください: {pointer!r}")

    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    current: JsonValue = value
    for token in tokens[:-1]:
        # 現在値が辞書ならキー、配列なら範囲内の数値添字をたどる。
        # 存在しない経路は削除不要として戻る。
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return

    final = tokens[-1]
    # 最終位置が辞書キーまたは範囲内配列添字なら削除し、存在しない場合は比較対象を変えない。
    if isinstance(current, dict):
        current.pop(final, None)
    elif isinstance(current, list) and final.isdigit() and int(final) < len(current):
        current.pop(int(final))


def _assert_error_schema(name: str, body: JsonValue, implementation: str) -> None:
    """エラー応答がerrorだけを持ち、code・message・任意detailsの型を満たすことを確認する。"""
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
    # detailsが存在する応答だけ配列型を要求し、省略可能なケースでは存在自体を強制しない。
    if "details" in error:
        assert isinstance(error["details"], list), (
            f"{name}: {implementation} error.details must be an array"
        )
