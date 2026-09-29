"""Fail-closed production startup checks."""

from __future__ import annotations

import ipaddress
import json

from cryptography.fernet import Fernet

from .config import Settings
from .repository import Repository

_DEFAULT_SECRETS = {"", "change-me", "change-this-in-production"}


def is_loopback_binding(host: str) -> bool:
    """Return whether a configured listen host is limited to this machine."""

    value = host.strip().lower().strip("[]")
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _tenant_api_keys(config: Settings) -> list[str]:
    if config.api_key:
        return [config.api_key]
    if not config.tenants_json.strip():
        return []
    try:
        values = json.loads(config.tenants_json)
        if not isinstance(values, list):
            raise TypeError
        return [item["api_key"] for item in values if item.get("id")]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "AIGC_LITE_TENANTS_JSON must contain tenant ids and API keys"
        ) from exc


def validate_startup_security(
    config: Settings,
    repository: Repository | None = None,
) -> None:
    """Reject unsafe defaults whenever the process listens beyond loopback."""

    if is_loopback_binding(config.host):
        return
    failures: list[str] = []
    if config.allow_signup:
        failures.append("AIGC_LITE_ALLOW_SIGNUP must be false")
    if config.auth_secret in _DEFAULT_SECRETS or len(config.auth_secret) < 32:
        failures.append("AIGC_LITE_AUTH_SECRET must be a non-default 32+ char secret")
    try:
        Fernet(config.master_key.encode())
    except (TypeError, ValueError):
        failures.append("AIGC_LITE_MASTER_KEY must be a valid Fernet key")

    api_keys = _tenant_api_keys(config)
    if any(key in _DEFAULT_SECRETS or len(key) < 16 for key in api_keys):
        failures.append("tenant API keys must be non-default and at least 16 chars")
    if bool(config.admin_email) != bool(config.admin_password):
        failures.append(
            "AIGC_LITE_ADMIN_EMAIL and AIGC_LITE_ADMIN_PASSWORD must be set together"
        )
    if config.admin_password and len(config.admin_password) < 12:
        failures.append("AIGC_LITE_ADMIN_PASSWORD must be at least 12 chars")
    if repository is not None and not api_keys and repository.count_users() == 0:
        failures.append(
            "configure a bootstrap administrator or tenant API key before public binding"
        )
    if failures:
        raise RuntimeError(
            "Unsafe non-loopback startup refused: " + "; ".join(failures)
        )
