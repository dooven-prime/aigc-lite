"""Programmatic Alembic entry point shared by SQLite and PostgreSQL repositories."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parent


def _config(database_url: str = "sqlite://") -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@lru_cache(maxsize=1)
def current_schema_head() -> str:
    """Return the single schema head packaged with this application."""

    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:
        raise RuntimeError("No Alembic schema head is configured")
    return head


def upgrade_database(database_url: str, *, connection: Any | None = None) -> None:
    config = _config(database_url)
    if connection is not None:
        config.attributes["connection"] = connection
    command.upgrade(config, "head")
