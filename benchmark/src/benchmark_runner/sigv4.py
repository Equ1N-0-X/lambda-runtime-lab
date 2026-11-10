"""API Gatewayへ送るHTTP要求へAWS Signature Version 4認証ヘッダーを付与する。"""

from __future__ import annotations

from collections.abc import Mapping

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import ReadOnlyCredentials


class SigningError(RuntimeError):
    """認証情報がなく、要求へ署名できない場合に通知する。"""


def credential_source(session: object) -> object:
    """AWS標準認証チェーンの認証情報を、期限前に更新できる形のまま取得する。

    一時的な認証情報は期限が切れるため、計測の開始時に固定せず、署名のたびに
    snapshot() で最新の値を取り出す。
    """
    credentials = session.get_credentials()  # type: ignore[attr-defined]
    # 認証チェーンのどこからも認証情報を取得できない場合は、未署名要求を送らず停止する。
    if credentials is None:
        raise SigningError("AWS標準認証チェーンから認証情報を取得できませんでした")
    return credentials


def snapshot(source: object) -> ReadOnlyCredentials:
    """署名一回分の認証情報を取り出す。

    キー・シークレット・トークンの3項目は同じ時点の組でなければ署名が無効になるため、
    botocoreが一貫した組として返す get_frozen_credentials を使う（期限が近ければ更新される）。
    更新に失敗した場合は、原因を含めて SigningError にする。
    """
    try:
        return source.get_frozen_credentials()  # type: ignore[attr-defined]
    # 期限切れ後の更新失敗（SSOの再ログインが必要など）は、利用者が対処できる署名失敗として扱う。
    except Exception as exc:
        raise SigningError(
            f"AWSの認証情報を更新できませんでした（{type(exc).__name__}: {exc}）"
        ) from exc


def sign_headers(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    credentials: ReadOnlyCredentials,
    region: str,
    service: str = "execute-api",
) -> dict[str, str]:
    """既存ヘッダーと本文を含めて要求を署名し、送信可能なヘッダー辞書を返す。"""
    request = AWSRequest(method=method, url=url, data=body, headers=dict(headers))
    SigV4Auth(credentials, service, region).add_auth(request)
    return dict(request.headers.items())
