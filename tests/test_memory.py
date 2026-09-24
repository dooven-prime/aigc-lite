import sqlite3

from fastapi.testclient import TestClient

from app import database, main
from app.repository import SQLiteRepository


def test_memory_search_covers_messages_documents_and_run_steps(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "memory.db")
    repository.init()
    session = repository.create_session("workspace-a", "Architecture notes")
    repository.add_message(
        "workspace-a", session["id"], "user", "We selected a searchable execution ledger"
    )
    repository.create_document(
        "workspace-a",
        "design.md",
        "The execution ledger stores durable evidence.",
        [{"index": 0, "content": "The execution ledger stores durable evidence.", "vector": [1.0]}],
    )
    run = repository.create_run(
        "workspace-a", session["id"], "request-1", None, "test-model"
    )
    repository.append_run_step(
        "workspace-a",
        run["id"],
        1,
        "tool",
        "research",
        "succeeded",
        "find ledger references",
        "execution ledger result",
    )
    repository.finish_run("workspace-a", run["id"], "succeeded")

    results = repository.search_memory("workspace-a", "execution ledger", 20)

    assert {item["kind"] for item in results} == {"message", "document", "run_step"}
    assert all(item["score"] > 0 for item in results)
    assert repository.search_memory("workspace-b", "execution ledger", 20) == []


def test_run_reads_are_workspace_scoped(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "runs.db")
    repository.init()
    session = repository.create_session("workspace-a", "Private")
    run = repository.create_run(
        "workspace-a", session["id"], "request-1", None, "test-model"
    )

    assert repository.get_run("workspace-a", run["id"])["id"] == run["id"]
    assert repository.get_run("workspace-b", run["id"]) is None
    assert repository.list_runs("workspace-b") == []


def test_run_and_search_http_surface(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "memory-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)

    with TestClient(main.app) as client:
        repository = database.get_repository()
        session = repository.create_session("default", "Searchable session")
        repository.add_message(
            "default", session["id"], "assistant", "unique searchable memory"
        )
        run = repository.create_run(
            "default", session["id"], "request-api", None, "test-model"
        )
        repository.append_run_step(
            "default",
            run["id"],
            1,
            "agent",
            "chat_agent",
            "succeeded",
            "",
            "unique searchable result",
        )
        repository.finish_run("default", run["id"], "succeeded")

        listed = client.get("/api/runs")
        detail = client.get(f"/api/runs/{run['id']}")
        searched = client.get("/api/search?q=unique+searchable")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run["id"]
    assert detail.status_code == 200
    assert detail.json()["steps"][0]["output_content"] == "unique searchable result"
    assert searched.status_code == 200
    assert {item["kind"] for item in searched.json()} == {"message", "run_step"}
