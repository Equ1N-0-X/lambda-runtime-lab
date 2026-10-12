"""API・検証・サービス層で共有する、公開エラー応答の分類を定義する。

ここにあるコード、メッセージ、HTTP状態、詳細文字列はOpenAPI契約で固定されているため、
内部コメントやログとは異なり英語のまま維持する。
"""

from collections.abc import Sequence

from app.models import ErrorDetail

VALIDATION_MESSAGE = "request validation failed"


class ApplicationError(Exception):
    """公開可能なHTTP状態・エラーコード・詳細を保持する業務例外。"""

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
    """入力順に依存しない応答にするため、詳細をフィールド名と理由で安定ソートする。"""
    return sorted(details, key=lambda detail: (detail.field, detail.reason))


def validation_error(details: Sequence[ErrorDetail]) -> ApplicationError:
    """入力内容が契約を満たさない場合の422エラーを作る。"""
    return ApplicationError(
        status_code=422,
        code="VALIDATION_ERROR",
        message=VALIDATION_MESSAGE,
        details=canonical_order(details),
    )


def unsupported_profile(supported_values: Sequence[str]) -> ApplicationError:
    """要求された実行方式が、このAPIで許可されていない場合の400エラーを作る。"""
    return ApplicationError(
        status_code=400,
        code="UNSUPPORTED_PROFILE",
        message="profile is not supported",
        details=[
            ErrorDetail(field="profile", reason=f"supported_values:{','.join(supported_values)}")
        ],
    )


def not_found(item_id: str) -> ApplicationError:
    """検証済みIDに対応する保存データがない場合の404エラーを作る。"""
    return ApplicationError(
        status_code=404,
        code="NOT_FOUND",
        message="item not found",
        details=[ErrorDetail(field="id", reason=f"not_found:{item_id}")],
    )


def dependency_error() -> ApplicationError:
    """外部保存先の障害で要求を完了できない場合の502エラーを作る。"""
    return ApplicationError(
        status_code=502,
        code="DEPENDENCY_ERROR",
        message="dependency error",
    )


def internal_error() -> ApplicationError:
    """内部計算など予期しない障害で要求を完了できない場合の500エラーを作る。"""
    return ApplicationError(
        status_code=500,
        code="INTERNAL_ERROR",
        message="internal error",
    )
