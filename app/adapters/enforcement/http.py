"""Strict HTTP transport for an independently operated policy enforcer."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from ...core.enforcement import (
    EnforcementSignatureAlgorithm,
    EnforcementTargetType,
    EnforcerRequest,
    SignedEnforcementReceiptEnvelope,
)
from ...core.errors import EnforcerRequestError, InvalidEnforcementReceiptError

ClientFactory = Callable[..., httpx.AsyncClient]
HeaderProvider = Callable[[], Mapping[str, str]]

_MAX_RESPONSE_BYTES = 512_000


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HTTPEnforcerAdapter:
    """Send frozen requests without redirects, proxy inheritance, or response trust."""

    def __init__(
        self,
        *,
        adapter_id: str,
        issuer_id: str,
        target_type: EnforcementTargetType,
        target_id: str,
        endpoint: str,
        timeout_seconds: float = 30.0,
        allow_insecure_loopback: bool = False,
        header_provider: HeaderProvider | None = None,
        client_factory: ClientFactory = httpx.AsyncClient,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Enforcer endpoint must be HTTP(S) without credentials, query, or fragment"
            )
        if parsed.scheme != "https" and not (
            allow_insecure_loopback and _is_loopback(parsed.hostname)
        ):
            raise ValueError("Enforcer endpoint must use HTTPS")
        if not 0.1 <= timeout_seconds <= 300:
            raise ValueError("Enforcer timeout must be between 0.1 and 300 seconds")
        if not 1_024 <= max_response_bytes <= 2_000_000:
            raise ValueError("Enforcer response limit is invalid")
        self.adapter_id = adapter_id
        self.issuer_id = issuer_id
        self.target_type = target_type
        self.target_id = target_id
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self._header_provider = header_provider
        self._client_factory = client_factory
        self._max_response_bytes = max_response_bytes

    async def enforce(
        self, request: EnforcerRequest
    ) -> SignedEnforcementReceiptEnvelope:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "aigc-lite-enforcer-adapter/1",
        }
        if self._header_provider is not None:
            supplied = dict(self._header_provider())
            if any(
                not isinstance(name, str)
                or not isinstance(value, str)
                or name.casefold() in {"host", "content-length", "transfer-encoding"}
                or "\r" in name
                or "\n" in name
                or "\r" in value
                or "\n" in value
                for name, value in supplied.items()
            ):
                raise EnforcerRequestError("Enforcer authentication headers are invalid")
            headers.update(supplied)
        body = request.as_dict()
        try:
            async with self._client_factory(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                headers=headers,
            ) as client:
                async with client.stream("POST", self.endpoint, json=body) as response:
                    if response.status_code != 200:
                        raise EnforcerRequestError(
                            "External enforcer rejected the request"
                        )
                    media_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if media_type.casefold() not in {
                        "application/json",
                        "application/problem+json",
                    }:
                        raise InvalidEnforcementReceiptError(
                            "content_type", "Enforcer response must be JSON"
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self._max_response_bytes:
                            raise InvalidEnforcementReceiptError(
                                "response", "Enforcer response is too large"
                            )
                        chunks.append(chunk)
        except (EnforcerRequestError, InvalidEnforcementReceiptError):
            raise
        except httpx.HTTPError as exc:
            raise EnforcerRequestError("External enforcer request failed") from exc

        try:
            value: Any = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidEnforcementReceiptError(
                "response", "Enforcer response is not valid JSON"
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "contract_version",
            "algorithm",
            "key_id",
            "payload",
            "signature",
        }:
            raise InvalidEnforcementReceiptError(
                "response", "Enforcer signature envelope fields are invalid"
            )
        try:
            algorithm = EnforcementSignatureAlgorithm(value["algorithm"])
        except (TypeError, ValueError) as exc:
            raise InvalidEnforcementReceiptError(
                "algorithm", "Enforcer signature algorithm is unsupported"
            ) from exc
        if not isinstance(value["payload"], dict):
            raise InvalidEnforcementReceiptError(
                "payload", "Enforcer signed payload must be an object"
            )
        if not isinstance(value["key_id"], str) or not isinstance(
            value["signature"], str
        ):
            raise InvalidEnforcementReceiptError(
                "response", "Enforcer signature envelope is invalid"
            )
        return SignedEnforcementReceiptEnvelope(
            contract_version=value["contract_version"],
            algorithm=algorithm,
            key_id=value["key_id"],
            payload=value["payload"],
            signature=value["signature"],
        )
