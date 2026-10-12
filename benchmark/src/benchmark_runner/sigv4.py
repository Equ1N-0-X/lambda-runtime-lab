"""API Gatewayへ送るHTTP要求へAWS Signature Version 4認証ヘッダーを付与する。"""

from __future__ import annotations

from collections.abc import Mapping

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import ReadOnlyCredentials


class SigningError(RuntimeError):
    """認証情報がなく、要求へ署名できない場合に通知する。"""


def frozen_credentials(session: object) -> ReadOnlyCredentials:
    """実行中に更新されない形式で、AWS標準認証チェーンの認証情報を取得する。"""
    credentials = session.get_credentials()  # type: ignore[attr-defined]
    # 認証チェーンのどこからも認証情報を取得できない場合は、未署名要求を送らず停止する。
    if credentials is None:
        raise SigningError("AWS標準認証チェーンから認証情報を取得できませんでした")
    return credentials.get_frozen_credentials()


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
