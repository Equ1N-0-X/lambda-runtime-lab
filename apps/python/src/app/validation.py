"""スキーマだけでは表せないリクエストの業務上の検証を行う。

重複、計算量上限、実行プロファイルを契約で定めた順序で検査し、PythonとRustで同じ
エラー詳細を返す。
"""

import re
from collections.abc import Sequence

from app.errors import unsupported_profile, validation_error
from app.models import (
    ITEM_ID_PATTERN,
    MAX_COMPUTE_WORK,
    AggregateRequest,
    ComputeRequest,
    ErrorDetail,
    TransformRequest,
)

COMPUTE_PROFILES = ("sequential",)
AGGREGATE_PROFILES = ("sequential", "concurrent")

BENCHMARK_RUN_ID_HEADER = "x-benchmark-run-id"
BENCHMARK_RUN_ID_FIELD = "headers.X-Benchmark-Run-Id"
# 計測相関ヘッダーはBenchmark Runnerの実行IDと同じ文字種を受け付けるため、UUIDだけでなく
# 人が読める実行IDも利用できる。
_BENCHMARK_RUN_ID = re.compile(ITEM_ID_PATTERN)


def validate_benchmark_run_id(run_id: str | None) -> None:
    """指定された相関IDだけを検証し、未指定の場合は通常リクエストとして受け付ける。"""

    # ヘッダーが存在し、かつ許可文字・長さの正規表現に一致しない場合だけ、
    # ヘッダーの検証エラーにする。
    if run_id is not None and _BENCHMARK_RUN_ID.fullmatch(run_id) is None:
        raise validation_error([ErrorDetail(field=BENCHMARK_RUN_ID_FIELD, reason="pattern")])


def _duplicate_indexes(values: Sequence[str]) -> list[int]:
    """先に現れた値を基準にし、二回目以降に現れた同一値の添字を返す。"""
    seen: set[str] = set()
    duplicates: list[int] = []
    for index, value in enumerate(values):
        # 既出集合に値がある場合は重複位置として記録し、ない場合は以後の比較対象に加える。
        if value in seen:
            duplicates.append(index)
        else:
            seen.add(value)
    return duplicates


def validate_transform(request: TransformRequest) -> None:
    """変換対象レコードのID重複を検出する。"""
    duplicates = _duplicate_indexes([record.id for record in request.records])
    # 重複IDが一件以上ある場合は、重複した各レコード位置をまとめて検証エラーにする。
    if duplicates:
        raise validation_error(
            [ErrorDetail(field=f"records[{index}].id", reason="duplicate") for index in duplicates]
        )


def validate_compute(request: ComputeRequest) -> None:
    """計算量と実行方式が、比較実験で許可した範囲に収まることを確認する。"""

    # 項目数と反復回数の積が上限を超える場合は、Lambdaを過度に消費する計算要求として拒否する。
    if request.items * request.iterations > MAX_COMPUTE_WORK:
        raise validation_error([ErrorDetail(field="$", reason=f"maximum_work:{MAX_COMPUTE_WORK}")])
    # プロファイル名が計算APIで許可した方式に含まれない場合は、利用可能な候補を示して拒否する。
    if request.profile not in COMPUTE_PROFILES:
        raise unsupported_profile(COMPUTE_PROFILES)


def validate_aggregate(request: AggregateRequest) -> None:
    """集約対象の重複と、読み取り方式の指定を確認する。"""
    duplicates = _duplicate_indexes(request.ids)
    # 同じIDを複数回集約すると値を二重計上するため、重複した添字がある場合は要求を拒否する。
    if duplicates:
        raise validation_error(
            [ErrorDetail(field=f"ids[{index}]", reason="duplicate") for index in duplicates]
        )
    # プロファイル名が直列または並行の許可方式に含まれない場合は、実行方式を選び直させる。
    if request.profile not in AGGREGATE_PROFILES:
        raise unsupported_profile(AGGREGATE_PROFILES)
