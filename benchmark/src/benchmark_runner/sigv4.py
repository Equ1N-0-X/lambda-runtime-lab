from __future__ import annotations

from collections.abc import Mapping

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import ReadOnlyCredentials


class SigningError(RuntimeError):
    """Raised when a request cannot be signed."""


def frozen_credentials(session: object) -> ReadOnlyCredentials:
    credentials = session.get_credentials()  # type: ignore[attr-defined]
    if credentials is None:
        raise SigningError("AWS credentials were not found in the credential chain")
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
    request = AWSRequest(method=method, url=url, data=body, headers=dict(headers))
    SigV4Auth(credentials, service, region).add_auth(request)
    return dict(request.headers.items())
