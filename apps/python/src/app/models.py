"""OpenAPI契約に対応する入力・出力データモデルと、検証上限を定義する。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

MAX_RECORDS = 1_000
MAX_TAGS = 32
MAX_AGGREGATE_IDS = 100
MAX_COMPUTE_ITEMS = 1_000
MAX_COMPUTE_ITERATIONS = 1_000_000
MAX_COMPUTE_WORK = 1_000_000

# PydanticはRustの正規表現エンジンでコンパイルし、`$`は改行前ではなく入力末尾へ一致する。
# OpenAPIのJSON SchemaとRust実装の文字判定を同じ結果にするため、この差異を前提にする。
ITEM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
PRINTABLE_ASCII_PATTERN = r"^[ -~]*$"

ItemId = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=ITEM_ID_PATTERN)]
RecordName = Annotated[StrictStr, Field(max_length=256, pattern=PRINTABLE_ASCII_PATTERN)]
Tag = Annotated[StrictStr, Field(max_length=64, pattern=PRINTABLE_ASCII_PATTERN)]
ItemValue = Annotated[StrictInt, Field(ge=-1_000_000_000, le=1_000_000_000)]
Payload = Annotated[StrictStr, Field(max_length=4_096)]
Seed = Annotated[StrictStr, Field(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    """暗黙の型変換と未定義フィールドを許可しない、全公開モデルの共通基底。"""

    model_config = ConfigDict(strict=True, extra="forbid")


class TransformRecord(StrictModel):
    """変換前後で共有する、ID・名前・タグ・数値を持つ一レコード。"""

    id: ItemId
    name: RecordName
    tags: Annotated[list[Tag], Field(max_length=MAX_TAGS)]
    value: ItemValue


class TransformRequest(StrictModel):
    """一件以上、上限件数以下の変換対象レコードを受け取る。"""

    records: Annotated[list[TransformRecord], Field(min_length=1, max_length=MAX_RECORDS)]


class TransformResponse(StrictModel):
    """正規化・ID整列済みレコードと、その件数を返す。"""

    records: list[TransformRecord]
    count: int


class ComputeRequest(StrictModel):
    """項目数、反復回数、seed、実行方式からCPU計算条件を表す。"""

    items: Annotated[StrictInt, Field(ge=1, le=MAX_COMPUTE_ITEMS)]
    iterations: Annotated[StrictInt, Field(ge=1, le=MAX_COMPUTE_ITERATIONS)]
    seed: Seed
    profile: StrictStr


class ComputeResponse(StrictModel):
    """実際の処理項目数・反復回数と、最終チェックサムを返す。"""

    processed: int
    iterations: int
    checksum: str


class Item(StrictModel):
    """DynamoDBで共有するID、集計値、固定長payloadを表す。"""

    id: ItemId
    value: ItemValue
    payload: Payload


class ItemResponse(StrictModel):
    """ID指定で取得した一項目を返す。"""

    item: Item


class AggregateRequest(StrictModel):
    """重複しないID一覧と、直列・並行の読み取り方式を受け取る。"""

    ids: Annotated[list[ItemId], Field(min_length=1, max_length=MAX_AGGREGATE_IDS)]
    profile: StrictStr


class AggregateResponse(StrictModel):
    """ID順で集約した件数、合計値、payloadチェックサムを返す。"""

    count: int
    total_value: int
    checksum: str


class HealthResponse(StrictModel):
    """Lambdaが要求を処理できる場合に固定状態okを返す。"""

    status: Literal["ok"]


class ErrorDetail(StrictModel):
    """入力または対象IDの位置と、機械判定可能な失敗理由を表す。"""

    field: str
    reason: str


class ErrorBody(StrictModel):
    """公開エラーコード、固定メッセージ、任意の詳細一覧を表す。"""

    code: str
    message: str
    details: list[ErrorDetail]


class ErrorResponse(StrictModel):
    """全失敗応答をerrorキー一つに統一する外側の構造。"""

    error: ErrorBody
