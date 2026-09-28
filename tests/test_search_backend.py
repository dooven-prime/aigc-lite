import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.contracts import RequestContext
from app.repository import SQLiteRepository
from app.services.memory import MemoryService


def _alembic_config(database_path: Path) -> Config:
    app_root = Path(__file__).resolve().parents[1] / "app"
    config = Config(str(app_root / "alembic.ini"))
    config.set_main_option("script_location", str(app_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_fts_migration_backfills_existing_execution_memory(tmp_path) -> None:
    path = tmp_path / "backfill.db"
    config = _alembic_config(path)
    command.upgrade(config, "0005_artifacts_citations")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO sessions(id, tenant_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("session-1", "workspace-a", "Existing session", "now", "now"),
        )
        db.execute(
            "INSERT INTO messages(session_id, tenant_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "session-1",
                "workspace-a",
                "user",
                "pre migration searchable marker",
                "now",
            ),
        )

    command.upgrade(config, "head")
    repository = SQLiteRepository(path)

    results = repository.search_memory("workspace-a", "migration searchable", 20)

    assert [item["kind"] for item in results] == ["message"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM search_entries").fetchone()[0] == 1


def test_sqlite_fts_searches_beyond_legacy_candidate_window(tmp_path) -> None:
    path = tmp_path / "fts-window.db"
    repository = SQLiteRepository(path)
    repository.init()
    session = repository.create_session("workspace-a", "Large history")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO messages(session_id, tenant_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session["id"],
                "workspace-a",
                "user",
                "oldest needle-outside-window",
                "0000",
            ),
        )
        db.executemany(
            "INSERT INTO messages(session_id, tenant_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    session["id"],
                    "workspace-a",
                    "user",
                    f"ordinary message {index}",
                    f"{index + 1:04d}",
                )
                for index in range(600)
            ],
        )

    results = repository.search_memory(
        "workspace-a", "needle-outside-window", 20
    )

    assert repository.search_backend().backend_id == "sqlite_fts5"
    assert [item["content"] for item in results] == [
        "oldest needle-outside-window"
    ]
    assert repository.search_memory(
        "workspace-b", "needle-outside-window", 20
    ) == []


def test_search_index_tracks_updates_deletes_and_safe_queries(tmp_path) -> None:
    path = tmp_path / "fts-sync.db"
    repository = SQLiteRepository(path)
    repository.init()
    session = repository.create_session("workspace-a", "Mutable title")
    repository.add_message(
        "workspace-a", session["id"], "user", "original searchable content"
    )
    with sqlite3.connect(path) as db:
        message_id = db.execute("SELECT id FROM messages").fetchone()[0]
        db.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("replacement indexed content", message_id),
        )

    assert repository.search_memory("workspace-a", "replacement indexed", 20)
    assert repository.search_memory("workspace-a", "original searchable", 20) == []
    assert repository.search_memory(
        "workspace-a", 'replacement OR title:secret* "', 20
    )

    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    assert repository.search_memory("workspace-a", "replacement indexed", 20) == []


def test_short_cjk_query_and_missing_fts_use_lexical_fallback(tmp_path) -> None:
    path = tmp_path / "fts-fallback.db"
    repository = SQLiteRepository(path)
    repository.init()
    session = repository.create_session("workspace-a", "中文检索")
    repository.add_message(
        "workspace-a", session["id"], "user", "这是执行记忆测试"
    )

    assert repository.search_memory("workspace-a", "执行", 20)
    assert repository.search_memory("workspace-a", "执行记忆", 20)

    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE search_entries_fts")

    assert repository.search_memory("workspace-a", "执行记忆", 20)


def test_memory_service_accepts_an_independent_search_backend(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "injected-search.db")
    repository.init()
    calls = []

    class FakeSearchBackend:
        backend_id = "fake"

        def search(self, workspace_id: str, query: str, limit: int = 20):
            calls.append((workspace_id, query, limit))
            return [{"id": "hit", "kind": "fake"}]

    service = MemoryService(
        repository_provider=lambda: repository,
        search_backend_provider=lambda: FakeSearchBackend(),
    )
    context = RequestContext(request_id="search", workspace_id="workspace-a")

    assert service.search(context, "query", 7) == [{"id": "hit", "kind": "fake"}]
    assert calls == [("workspace-a", "query", 7)]
