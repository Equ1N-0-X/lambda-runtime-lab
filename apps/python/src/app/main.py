import json
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain import compute_checksum, transform_records
from app.errors import ApplicationError, validation_error
from app.logging import log_internal_error, log_request_completed
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
    parts = list(location)
    if not parts:
        return "$"

    result = ""
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        elif result:
            result += f".{part}"
        else:
            result = str(part)
    return result or "$"


def _reason(error: dict[str, Any]) -> str:
    error_type = error["type"]
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
    return [
        ErrorDetail(
            field=field_override or _field_name(item["loc"]),
            reason=_reason(item),
        )
        for item in error.errors()
    ]


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object member: {key}")
        result[key] = value
    return result


def _parse_body[RequestModel: BaseModel](model: type[RequestModel], body: bytes) -> RequestModel:
    """Decode and validate a request body under the canonical taxonomy.

    The body is parsed once here instead of relying on framework-level parsing
    so that JSON, structural, and boundary failures map onto the exact fields
    and reasons the Rust implementation returns.
    """
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_members)
    except (UnicodeDecodeError, ValueError):
        raise validation_error([ErrorDetail(field="$", reason="invalid_json")]) from None

    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise validation_error(_details(error)) from None


def _require_valid_benchmark_run_id(request: Request) -> None:
    validate_benchmark_run_id(request.headers.get(BENCHMARK_RUN_ID_HEADER))


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    response = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))


def create_app(repository: ItemRepository | None = None) -> FastAPI:
    application = FastAPI(title="Lambda Runtime Lab", version="1.0.0")
    item_service = ItemService(DYNAMODB_REPOSITORY if repository is None else repository)

    @application.middleware("http")
    async def structured_request_log(request: Request, call_next: Any) -> JSONResponse:
        response = await call_next(request)
        log_request_completed(request, response.status_code)
        return response

    @application.exception_handler(ApplicationError)
    async def application_error_handler(_request: Request, error: ApplicationError) -> JSONResponse:
        return _error_response(
            error.status_code,
            error.code,
            error.message,
            error.details,
        )

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(_request: Request, _error: RepositoryError) -> JSONResponse:
        return _error_response(502, "DEPENDENCY_ERROR", "dependency error")

    @application.exception_handler(Exception)
    async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
        log_internal_error(request, error)
        return _error_response(500, "INTERNAL_ERROR", "internal error")

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        _require_valid_benchmark_run_id(request)
        return HealthResponse(status="ok")

    @application.post("/transform", response_model=TransformResponse)
    async def transform(request: Request) -> TransformResponse:
        _require_valid_benchmark_run_id(request)
        body = _parse_body(TransformRequest, await request.body())
        validate_transform(body)
        return transform_records(body.records)

    @application.post("/compute", response_model=ComputeResponse)
    async def compute(request: Request) -> ComputeResponse:
        _require_valid_benchmark_run_id(request)
        body = _parse_body(ComputeRequest, await request.body())
        request.state.profile = body.profile
        validate_compute(body)
        return compute_checksum(body.items, body.iterations, body.seed)

    @application.get("/items/{id}", response_model=ItemResponse)
    async def get_item(id: str, request: Request) -> ItemResponse:
        _require_valid_benchmark_run_id(request)
        try:
            item_id = _ITEM_ID_ADAPTER.validate_python(id)
        except ValidationError as error:
            raise validation_error(_details(error, field_override="id")) from None
        return ItemResponse(item=await item_service.get_item(item_id))

    @application.post("/aggregate", response_model=AggregateResponse)
    async def aggregate(request: Request) -> AggregateResponse:
        _require_valid_benchmark_run_id(request)
        body = _parse_body(AggregateRequest, await request.body())
        request.state.profile = body.profile
        validate_aggregate(body)
        return await item_service.aggregate(body.ids, body.profile)

    return application


app = create_app()
handler = Mangum(app, lifespan="off")
