"""HTTPリクエストを共通API契約に従うドメイン処理へ接続する。

フレームワーク固有の入力・例外を正規化し、PythonとRustで同一の成功・失敗応答を返す。
"""

import json
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain import compute_checksum, transform_records
from app.errors import (
    MAX_BODY_BYTES,
    MAX_JSON_DEPTH,
    ApplicationError,
    payload_too_large,
    validation_error,
)
from app.logging import log_dependency_error, log_internal_error, log_request_completed
from app.models import (
    AggregateRequest,
    AggregateResponse,
    ComputeRequest,
    ComputeResponse,
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ItemId,
    ItemResponse,
    TransformRequest,
    TransformResponse,
)
from app.repositories import DYNAMODB_REPOSITORY, ItemRepository, RepositoryError
from app.services import ItemService
from app.validation import (
    BENCHMARK_RUN_ID_HEADER,
    validate_aggregate,
    validate_benchmark_run_id,
    validate_compute,
    validate_transform,
)

_ITEM_ID_ADAPTER = TypeAdapter(ItemId)

_TYPE_REASONS = {
    "bool_type": "type:boolean",
    "dict_type": "type:object",
    "float_type": "type:number",
    "int_type": "type:integer",
    "list_type": "type:array",
    "model_attributes_type": "type:object",
    "model_type": "type:object",
    "string_type": "type:string",
}


def _field_name(location: Sequence[Any]) -> str:
    """Pydanticの位置情報を、API契約で定めたフィールドパス表記へ変換する。"""
    parts = list(location)

    # 位置情報が空の場合は、個別フィールドではなくリクエスト全体のエラーとして "$" を返す。
    if not parts:
        return "$"

    result = ""
    for part in parts:
        # 配列の添字である整数なら直前のフィールド名へ角括弧で連結し、
        # オブジェクトの項目名ならドット区切りにして契約上のエラー位置を作る。
        if isinstance(part, int):
            result += f"[{part}]"
        elif result:
            result += f".{part}"
        else:
            result = str(part)
    return result or "$"


def _reason(error: dict[str, Any]) -> str:
    """Pydanticの実装依存エラー種別を、公開契約の reason 文字列へ変換する。"""
    error_type = error["type"]

    # 型不一致はPydanticの詳細な種別を公開せず、契約で固定した type:* の理由へ集約する。
    if error_type in _TYPE_REASONS:
        return _TYPE_REASONS[error_type]

    context = error.get("ctx", {})
    reasons = {
        "extra_forbidden": "additional_property_not_allowed",
        "greater_than_equal": f"minimum:{context.get('ge')}",
        "less_than_equal": f"maximum:{context.get('le')}",
        "missing": "required",
        "string_pattern_mismatch": "pattern",
        "string_too_long": f"max_length:{context.get('max_length')}",
        "string_too_short": f"min_length:{context.get('min_length')}",
        "too_long": f"max_items:{context.get('max_length')}",
        "too_short": f"min_items:{context.get('min_length')}",
    }
    return reasons.get(error_type, "invalid")


def _details(error: ValidationError, field_override: str | None = None) -> list[ErrorDetail]:
    """検証エラーを、フィールド名と理由だけからなる公開エラー詳細へ変換する。"""
    return [
        ErrorDetail(
            field=field_override or _field_name(item["loc"]),
            reason=_reason(item),
        )
        for item in error.errors()
    ]


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSONオブジェクトに同名キーがある場合、最後の値を採用せず入力エラーにする。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        # 既に同じキーを保持している場合は、JSON仕様上許容される上書きを採用せず、
        # 言語間で一貫して invalid_json と扱うため解析を中断する。
        if key in result:
            raise ValueError(f"JSONオブジェクトのキーが重複しています: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(name: str) -> None:
    """NaN・Infinityは標準のJSONではないため、Pythonの拡張として受け付けない。"""
    raise ValueError(f"JSONではない数値です: {name}")


def _reject_unsupported_values(value: object, depth: int = 0) -> None:
    """文字列がUnicodeのスカラー値だけでできていて、入れ子が上限の深さに収まるか確かめる。

    \\ud800 のような対になっていないサロゲートのエスケープは、Pythonでは文字列に残るが、
    正本ではUnicodeのスカラー値ではないためJSONの解析失敗として扱う。
    depth は、この値を囲んでいる配列・オブジェクトの数を表す。
    """
    # キーと値の文字列を再帰的に確かめ、数値・真偽値・nullは対象外とする。
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("Unicodeのスカラー値ではない文字が含まれています")
        return
    # 数値・真偽値・nullは、深さにも文字にも関係しないため確かめない。
    if not isinstance(value, dict | list):
        return
    # 既に上限の数だけ囲まれている配列・オブジェクトは、上限を超える入れ子として拒否する。
    if depth >= MAX_JSON_DEPTH:
        raise ValueError("JSONの入れ子が深すぎます")

    if isinstance(value, dict):
        for key, member in value.items():
            _reject_unsupported_values(key, depth + 1)
            _reject_unsupported_values(member, depth + 1)
    else:
        for member in value:
            _reject_unsupported_values(member, depth + 1)


def _parse_body[RequestModel: BaseModel](model: type[RequestModel], body: bytes) -> RequestModel:
    """本文を一度だけ解析し、JSON・構造・境界の失敗を共通エラー形式へ変換する。"""
    # 上限を超える本文は、解析に時間とメモリを使う前に413として拒否する。
    if len(body) > MAX_BODY_BYTES:
        raise payload_too_large()

    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_non_json_constant,
        )
        _reject_unsupported_values(payload)

    # UTF-8でない本文、JSON構文不正、重複キー、JSONではない数値、対のないサロゲート、
    # 上限を超える入れ子は、すべて本文全体のJSON不正として返す。標準の解析器は入れ子が
    # 極端に深いと RecursionError を送出するため、500にせず同じJSON不正として扱う。
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise validation_error([ErrorDetail(field="$", reason="invalid_json")]) from None

    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise validation_error(_details(error)) from None


def _require_valid_benchmark_run_id(request: Request) -> None:
    """任意の計測相関ヘッダーが指定された場合だけ、許可文字と長さを検証する。"""
    validate_benchmark_run_id(request.headers.get(BENCHMARK_RUN_ID_HEADER))


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    """内部例外を公開API契約のエラー本文とHTTP状態へ変換する。"""
    response = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))


def create_app(repository: ItemRepository | None = None) -> FastAPI:
    """依存リポジトリを差し替え可能なFastAPIアプリケーションを組み立てる。"""
    application = FastAPI(title="Lambda Runtime Lab", version="1.0.0")

    # テスト時にリポジトリが渡された場合はその実装を使い、指定がない本番時だけ
    # DynamoDB用の既定リポジトリを使う。
    item_service = ItemService(DYNAMODB_REPOSITORY if repository is None else repository)

    @application.middleware("http")
    async def structured_request_log(request: Request, call_next: Any) -> JSONResponse:
        """正常・仕様内エラーの応答を、計測で相関可能なINFOログとして残す。"""
        response = await call_next(request)

        # HTTP状態が500未満なら正常または利用者が修正可能な仕様内エラーとしてINFOを残す。
        # 500以上は例外ハンドラーがWARN/ERRORを出すため、ここでは重複ログを出さない。
        if response.status_code < 500:
            log_request_completed(request, response.status_code)

        return response

    @application.exception_handler(ApplicationError)
    async def application_error_handler(_request: Request, error: ApplicationError) -> JSONResponse:
        """入力不正・未検出など、仕様で定義された業務エラーをそのまま応答にする。"""
        return _error_response(
            error.status_code,
            error.code,
            error.message,
            error.details,
        )

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(request: Request, error: RepositoryError) -> JSONResponse:
        """DynamoDB障害を502へ変換し、原因の分類を運用調査用のWARNログに残す。"""
        log_dependency_error(request, category=error.category, error_code=error.error_code)
        return _error_response(502, "DEPENDENCY_ERROR", "dependency error")

    @application.exception_handler(Exception)
    async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
        """未分類の例外を500へ隠蔽し、原因分類をERRORログに記録する。"""
        log_internal_error(request, error)
        return _error_response(500, "INTERNAL_ERROR", "internal error")

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        """依存先へアクセスせず、実行基盤とAPI到達性を確認する。"""
        _require_valid_benchmark_run_id(request)
        return HealthResponse(status="ok")

    @application.post("/transform", response_model=TransformResponse)
    async def transform(request: Request) -> TransformResponse:
        """レコードを検証後に正規化し、並び順が一定のレスポンスを返す。"""
        _require_valid_benchmark_run_id(request)
        body = _parse_body(TransformRequest, await request.body())
        validate_transform(body)
        return transform_records(body.records)

    @application.post("/compute", response_model=ComputeResponse)
    async def compute(request: Request) -> ComputeResponse:
        """指定回数のハッシュ計算を実行し、計測プロファイルをログへ引き継ぐ。"""
        _require_valid_benchmark_run_id(request)
        body = _parse_body(ComputeRequest, await request.body())
        request.state.profile = body.profile
        validate_compute(body)
        return compute_checksum(body.items, body.iterations, body.seed)

    @application.get("/items/{id}", response_model=ItemResponse)
    async def get_item(id: str, request: Request) -> ItemResponse:
        """パスのIDを検証し、DynamoDBから1件だけ取得する。"""
        _require_valid_benchmark_run_id(request)
        try:
            item_id = _ITEM_ID_ADAPTER.validate_python(id)

        # パスIDが契約上の形式を満たさない場合は、本文ではなく id フィールドの検証失敗として返す。
        except ValidationError as error:
            raise validation_error(_details(error, field_override="id")) from None
        return ItemResponse(item=await item_service.get_item(item_id))

    @application.post("/aggregate", response_model=AggregateResponse)
    async def aggregate(request: Request) -> AggregateResponse:
        """指定プロファイルで複数IDを取得し、合計とチェックサムを返す。"""
        _require_valid_benchmark_run_id(request)
        body = _parse_body(AggregateRequest, await request.body())
        request.state.profile = body.profile
        validate_aggregate(body)
        return await item_service.aggregate(body.ids, body.profile)

    return application


app = create_app()
handler = Mangum(app, lifespan="off")
