"""Canonical error taxonomy shared by the API, validation, and service layers.

Every code, message, status, and detail string here is fixed by
`spec/openapi.yaml` so that the Rust implementation can return byte-identical
error bodies.
"""

from collections.abc import Sequence

from app.models import ErrorDetail

VALIDATION_MESSAGE = "request validation failed"


class ApplicationError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def canonical_order(details: Sequence[ErrorDetail]) -> list[ErrorDetail]:
    return sorted(details, key=lambda detail: (detail.field, detail.reason))


def validation_error(details: Sequence[ErrorDetail]) -> ApplicationError:
    return ApplicationError(
        status_code=422,
        code="VALIDATION_ERROR",
        message=VALIDATION_MESSAGE,
        details=canonical_order(details),
    )


def unsupported_profile(supported_values: Sequence[str]) -> ApplicationError:
    return ApplicationError(
        status_code=400,
        code="UNSUPPORTED_PROFILE",
        message="profile is not supported",
        details=[
            ErrorDetail(field="profile", reason=f"supported_values:{','.join(supported_values)}")
        ],
    )


def not_found(item_id: str) -> ApplicationError:
    return ApplicationError(
        status_code=404,
        code="NOT_FOUND",
        message="item not found",
        details=[ErrorDetail(field="id", reason=f"not_found:{item_id}")],
    )


def dependency_error() -> ApplicationError:
    return ApplicationError(
        status_code=502,
        code="DEPENDENCY_ERROR",
        message="dependency error",
    )


def internal_error() -> ApplicationError:
    return ApplicationError(
        status_code=500,
        code="INTERNAL_ERROR",
        message="internal error",
    )
