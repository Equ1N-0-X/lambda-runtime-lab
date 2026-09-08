"""Request checks that the JSON schema layer cannot express.

Pydantic covers structure, types, and boundaries. The canonical contract adds
duplicate detection, the compute work limit, and profile support, and fixes the
order in which they are applied.
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
# The correlation header accepts the same characters as a Benchmark Runner run
# identifier, so readable run IDs work as well as UUIDs.
_BENCHMARK_RUN_ID = re.compile(ITEM_ID_PATTERN)


def validate_benchmark_run_id(run_id: str | None) -> None:
    if run_id is not None and _BENCHMARK_RUN_ID.fullmatch(run_id) is None:
        raise validation_error([ErrorDetail(field=BENCHMARK_RUN_ID_FIELD, reason="pattern")])


def _duplicate_indexes(values: Sequence[str]) -> list[int]:
    seen: set[str] = set()
    duplicates: list[int] = []
    for index, value in enumerate(values):
        if value in seen:
            duplicates.append(index)
        else:
            seen.add(value)
    return duplicates


def validate_transform(request: TransformRequest) -> None:
    duplicates = _duplicate_indexes([record.id for record in request.records])
    if duplicates:
        raise validation_error(
            [ErrorDetail(field=f"records[{index}].id", reason="duplicate") for index in duplicates]
        )


def validate_compute(request: ComputeRequest) -> None:
    if request.items * request.iterations > MAX_COMPUTE_WORK:
        raise validation_error([ErrorDetail(field="$", reason=f"maximum_work:{MAX_COMPUTE_WORK}")])
    if request.profile not in COMPUTE_PROFILES:
        raise unsupported_profile(COMPUTE_PROFILES)


def validate_aggregate(request: AggregateRequest) -> None:
    duplicates = _duplicate_indexes(request.ids)
    if duplicates:
        raise validation_error(
            [ErrorDetail(field=f"ids[{index}]", reason="duplicate") for index in duplicates]
        )
    if request.profile not in AGGREGATE_PROFILES:
        raise unsupported_profile(AGGREGATE_PROFILES)
