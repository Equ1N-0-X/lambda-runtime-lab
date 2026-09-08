from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

MAX_RECORDS = 1_000
MAX_TAGS = 32
MAX_AGGREGATE_IDS = 100
MAX_COMPUTE_ITEMS = 1_000
MAX_COMPUTE_ITERATIONS = 1_000_000
MAX_COMPUTE_WORK = 1_000_000

# Pydantic compiles these with the Rust regex engine, where `$` anchors at the
# end of input rather than before a trailing newline. That matches the JSON
# Schema anchors in spec/openapi.yaml and the Rust character checks.
ITEM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
PRINTABLE_ASCII_PATTERN = r"^[ -~]*$"

ItemId = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=ITEM_ID_PATTERN)]
RecordName = Annotated[StrictStr, Field(max_length=256, pattern=PRINTABLE_ASCII_PATTERN)]
Tag = Annotated[StrictStr, Field(max_length=64, pattern=PRINTABLE_ASCII_PATTERN)]
ItemValue = Annotated[StrictInt, Field(ge=-1_000_000_000, le=1_000_000_000)]
Payload = Annotated[StrictStr, Field(max_length=4_096)]
Seed = Annotated[StrictStr, Field(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class TransformRecord(StrictModel):
    id: ItemId
    name: RecordName
    tags: Annotated[list[Tag], Field(max_length=MAX_TAGS)]
    value: ItemValue


class TransformRequest(StrictModel):
    records: Annotated[list[TransformRecord], Field(min_length=1, max_length=MAX_RECORDS)]


class TransformResponse(StrictModel):
    records: list[TransformRecord]
    count: int


class ComputeRequest(StrictModel):
    items: Annotated[StrictInt, Field(ge=1, le=MAX_COMPUTE_ITEMS)]
    iterations: Annotated[StrictInt, Field(ge=1, le=MAX_COMPUTE_ITERATIONS)]
    seed: Seed
    profile: StrictStr


class ComputeResponse(StrictModel):
    processed: int
    iterations: int
    checksum: str


class Item(StrictModel):
    id: ItemId
    value: ItemValue
    payload: Payload


class ItemResponse(StrictModel):
    item: Item


class AggregateRequest(StrictModel):
    ids: Annotated[list[ItemId], Field(min_length=1, max_length=MAX_AGGREGATE_IDS)]
    profile: StrictStr


class AggregateResponse(StrictModel):
    count: int
    total_value: int
    checksum: str


class HealthResponse(StrictModel):
    status: Literal["ok"]


class ErrorDetail(StrictModel):
    field: str
    reason: str


class ErrorBody(StrictModel):
    code: str
    message: str
    details: list[ErrorDetail]


class ErrorResponse(StrictModel):
    error: ErrorBody
