"""Bounded in-process fallback for stores without a native text index."""

from __future__ import annotations

from collections.abc import Callable

CandidateLoader = Callable[[str, int], list[dict]]


def rank_lexical(rows: list[dict], query: str, limit: int) -> list[dict]:
    """Apply the stable public result projection to lexical candidates."""
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    ranked = []
    for row in rows:
        content = str(row.get("content") or "")
        title = str(row.get("title") or "")
        haystack = f"{title}\n{content}".casefold()
        score = sum(haystack.count(term) for term in terms)
        if not score:
            continue
        ranked.append(
            {
                "id": str(row["id"]),
                "kind": row["kind"],
                "title": title,
                "content": content[:4000],
                "score": float(score),
                "created_at": row["created_at"],
                "session_id": row.get("session_id"),
                "run_id": row.get("run_id"),
                "step_id": row.get("step_id"),
                "artifact_id": row.get("artifact_id"),
                "source_kind": row.get("source_kind"),
                "import_batch_id": row.get("import_batch_id"),
                "conversation_id": row.get("conversation_id"),
            }
        )
    ranked.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
    return ranked[: max(1, min(limit, 50))]


class LexicalSearchBackend:
    """Compatibility backend over a bounded storage candidate loader."""

    backend_id = "lexical"

    def __init__(self, candidate_loader: CandidateLoader, candidate_limit: int = 500):
        self._candidate_loader = candidate_loader
        self._candidate_limit = candidate_limit

    def search(self, workspace_id: str, query: str, limit: int = 20) -> list[dict]:
        rows = self._candidate_loader(workspace_id, self._candidate_limit)
        return rank_lexical(rows, query, limit)
