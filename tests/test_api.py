import json
import sqlite3

from fastapi.testclient import TestClient

from app.database import create_document, init_db, search_documents
from app.main import app
from app.tools import tool


@tool("echo")
def echo(value: str) -> dict[str, str]:
    """Return a value for protocol smoke tests."""
    return {"value": value}


def test_ui_and_session_api(tmp_path, monkeypatch) -> None:
    from app import database

    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "test.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    init_db()
    with TestClient(app) as client:
        assert client.get("/ui/").status_code == 200
        created = client.post("/api/sessions", json={"title": "Test"})
        assert created.status_code == 200
        session_id = created.json()["id"]
        assert client.get(f"/api/sessions/{session_id}").json()["messages"] == []


def test_mcp_json_rpc() -> None:
    with TestClient(app) as client:
        initialized = client.post("/mcp-legacy", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert initialized.json()["result"]["serverInfo"]["name"] == "aigc-lite"
        tools = client.post("/mcp-legacy", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
        assert any(item["name"] == "echo" for item in tools["result"]["tools"])
        called = client.post("/mcp-legacy", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"value": "ok"}},
        }).json()
        assert json.loads(called["result"]["content"][0]["text"]) == {"value": "ok"}
        invalid = client.post("/mcp-legacy", json={"id": 4, "method": "tools/list"}).json()
        assert invalid["error"]["code"] == -32600


def test_knowledge_is_tenant_scoped(tmp_path, monkeypatch) -> None:
    from app import database

    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "knowledge.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    init_db()
    create_document("tenant-a", "a.md", "alpha project document")
    create_document("tenant-b", "b.md", "alpha private document")
    assert [item["name"] for item in search_documents("tenant-a", "alpha")] == ["a.md"]
    assert [item["name"] for item in search_documents("tenant-b", "alpha")] == ["b.md"]


def test_tenant_api_key_is_required_and_selects_tenant(monkeypatch) -> None:
    from app import tenancy

    monkeypatch.setattr(tenancy.settings, "api_key", "team-secret")
    monkeypatch.setattr(tenancy.settings, "tenants_json", "")
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401
        response = client.get("/api/sessions", headers={"Authorization": "Bearer team-secret"})
        assert response.status_code == 200
        assert all(item["tenant_id"] == "default" for item in response.json())


def test_document_api_and_session_ownership(tmp_path, monkeypatch) -> None:
    from app import database

    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    with TestClient(app) as client:
        document = client.post("/api/knowledge/documents", json={"name": "guide.md", "content": "SQLite guide"})
        assert document.status_code == 200
        results = client.get("/api/knowledge/search?q=SQLite").json()
        assert results[0]["name"] == "guide.md"
        session_id = client.post("/api/sessions", json={}).json()["id"]
        assert client.get(f"/api/sessions/{session_id}").status_code == 200
        assert client.get("/api/sessions/not-owned").status_code == 404
        assert client.get("/").status_code == 200
