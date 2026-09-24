"""Repository selection and compatibility functions for the HTTP layer."""

from __future__ import annotations

from .config import settings
from .repository import PostgresRepository, Repository, SQLiteRepository


def _connect():
    import sqlite3

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


_DEFAULT_CONNECT = _connect
_repository_cache: dict[tuple[str, int], Repository] = {}


def get_repository() -> Repository:
    url = settings.database_url
    cache_key = (url or str(settings.database_path), id(_connect))
    if cache_key in _repository_cache:
        return _repository_cache[cache_key]
    if url.startswith("postgresql"):
        repository = PostgresRepository(url)
    else:
        repository = SQLiteRepository(
            settings.database_path,
            connect_factory=None if _connect is _DEFAULT_CONNECT else _connect,
        )
    repository.init()
    _repository_cache[cache_key] = repository
    return repository


def init_db() -> None:
    get_repository().init()


def create_session(tenant_id: str, title: str = "New conversation") -> dict:
    return get_repository().create_session(tenant_id, title)


def list_sessions(tenant_id: str) -> list[dict]:
    return get_repository().list_sessions(tenant_id)


def get_session(tenant_id: str, session_id: str) -> dict | None:
    return get_repository().get_session(tenant_id, session_id)


def add_message(tenant_id: str, session_id: str, role: str, content: str) -> None:
    get_repository().add_message(tenant_id, session_id, role, content)


def create_document(tenant_id: str, name: str, content: str, chunks: list[dict] | None = None) -> dict:
    from .vectors import chunk_text, embed_text

    chunks = chunks or [
        {"index": index, "content": chunk, "vector": embed_text(chunk)}
        for index, chunk in enumerate(chunk_text(content))
    ]
    return get_repository().create_document(tenant_id, name, content, chunks)


def search_documents(tenant_id: str, query: str, limit: int = 5) -> list[dict]:
    from .vectors import embed_text

    return get_repository().search_chunks(tenant_id, query, embed_text(query), limit)
