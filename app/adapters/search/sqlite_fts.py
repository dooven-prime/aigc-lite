"""SQLite FTS5 search with a safe lexical fallback."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ...ports.search import SearchBackend

ConnectionFactory = Callable[[], sqlite3.Connection]


def _match_expression(query: str) -> str | None:
    """Quote user terms so FTS operators and column selectors stay inert."""
    terms = [term.strip() for term in query.split() if len(term.strip()) >= 3]
    if not terms:
        return None
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


class SQLiteFTS5SearchBackend:
    """Use the migration-managed FTS5 index, degrading safely when unavailable."""

    backend_id = "sqlite_fts5"

    def __init__(
        self,
        connect: ConnectionFactory,
        fallback: SearchBackend,
    ) -> None:
        self._connect = connect
        self._fallback = fallback

    def search(self, workspace_id: str, query: str, limit: int = 20) -> list[dict]:
        expression = _match_expression(query)
        if expression is None:
            return self._fallback.search(workspace_id, query, limit)
        bounded_limit = max(1, min(limit, 50))
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT e.source_id id, e.kind, e.title, e.content, "
                    "e.created_at, e.session_id, e.run_id, e.step_id, "
                    "e.artifact_id, e.source_kind, "
                    "bm25(search_entries_fts, 4.0, 1.0) rank "
                    "FROM search_entries_fts "
                    "JOIN search_entries e ON e.rowid = search_entries_fts.rowid "
                    "WHERE search_entries_fts MATCH ? AND e.tenant_id = ? "
                    "ORDER BY rank, e.created_at DESC LIMIT ?",
                    (expression, workspace_id, bounded_limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return self._fallback.search(workspace_id, query, limit)
        if not rows:
            # This retains compatibility for tokenizers without substring support.
            return self._fallback.search(workspace_id, query, limit)
        return [
            {
                "id": str(row["id"]),
                "kind": row["kind"],
                "title": str(row["title"] or ""),
                "content": str(row["content"] or "")[:4000],
                "score": max(0.000001, -float(row["rank"])),
                "created_at": row["created_at"],
                "session_id": row["session_id"],
                "run_id": row["run_id"],
                "step_id": row["step_id"],
                "artifact_id": row["artifact_id"],
                "source_kind": row["source_kind"],
            }
            for row in rows
        ]
