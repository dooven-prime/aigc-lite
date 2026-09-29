import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.startup import is_loopback_binding, validate_startup_security


class UserCountRepository:
    def __init__(self, count: int) -> None:
        self._count = count

    def count_users(self) -> int:
        return self._count


def _public_settings(**overrides) -> Settings:
    values = {
        "host": "0.0.0.0",
        "allow_signup": False,
        "auth_secret": "a" * 32,
        "master_key": Fernet.generate_key().decode(),
        "admin_email": "admin@example.test",
        "admin_password": "long-bootstrap-password",
    }
    values.update(overrides)
    return Settings(**values)


def test_loopback_binding_allows_local_development_defaults() -> None:
    assert is_loopback_binding("127.0.0.1")
    assert is_loopback_binding("::1")
    assert is_loopback_binding("localhost")
    validate_startup_security(Settings(host="127.0.0.1"))


def test_non_loopback_binding_rejects_insecure_defaults() -> None:
    with pytest.raises(RuntimeError, match="Unsafe non-loopback startup refused") as caught:
        validate_startup_security(
            Settings(
                host="0.0.0.0",
                allow_signup=True,
                auth_secret="change-this-in-production",
                master_key="",
            )
        )

    message = str(caught.value)
    assert "ALLOW_SIGNUP" in message
    assert "AUTH_SECRET" in message
    assert "MASTER_KEY" in message


def test_non_loopback_binding_requires_a_real_authentication_principal() -> None:
    config = _public_settings(admin_email="", admin_password="")
    with pytest.raises(RuntimeError, match="bootstrap administrator"):
        validate_startup_security(config, UserCountRepository(0))

    validate_startup_security(config, UserCountRepository(1))


def test_non_loopback_binding_accepts_hardened_bootstrap_configuration() -> None:
    validate_startup_security(_public_settings(), UserCountRepository(1))
