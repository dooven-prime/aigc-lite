"""Programmatic Alembic entry point shared by SQLite and PostgreSQL repositories."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent
_PROVISIONAL_REVISION_ALIASES = {
    "0017_execution_authority_evidence": "0017_execution_authority",
}


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


def _repair_provisional_sqlite_revision(connection: Any) -> None:
    """Relabel unreleased SQLite revision IDs that exceeded PostgreSQL's limit."""

    if connection.dialect.name != "sqlite":
        return
    try:
        has_version_table = connection.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'alembic_version'"
            )
        ).scalar_one_or_none()
        if has_version_table is not None:
            for provisional, released in _PROVISIONAL_REVISION_ALIASES.items():
                connection.execute(
                    text(
                        "UPDATE alembic_version SET version_num = :released "
                        "WHERE version_num = :provisional"
                    ),
                    {"released": released, "provisional": provisional},
                )
    finally:
        # The metadata probe starts SQLAlchemy's implicit transaction. Finish
        # it before Alembic opens and commits its migration transaction.
        connection.commit()


def upgrade_database(database_url: str, *, connection: Any | None = None) -> None:
    config = _config(database_url)
    if connection is not None:
        _repair_provisional_sqlite_revision(connection)
        config.attributes["connection"] = connection
    command.upgrade(config, "head")
