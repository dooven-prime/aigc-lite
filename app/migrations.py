"""Programmatic Alembic entry point shared by SQLite and PostgreSQL repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

_ROOT = Path(__file__).resolve().parent


def upgrade_database(database_url: str, *, connection: Any | None = None) -> None:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    if connection is not None:
        config.attributes["connection"] = connection
    command.upgrade(config, "head")
