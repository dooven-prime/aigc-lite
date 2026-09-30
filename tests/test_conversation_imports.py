import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import conversation_imports as import_api
from app.auth import current_admin_user
from app.core.contracts import RequestContext
from app.core.errors import InvalidConversationImportError
from app.repository import SQLiteRepository
from app.services.conversation_import_registry import ConversationImportRegistry
from app.services.conversation_imports import ConversationImportService
from app.services.qualification import QualificationService
from app.tenancy import Tenant, current_tenant


def _chatgpt_export(marker: str = "branch-only-marker") -> bytes:
    return json.dumps(
        [
            {
                "id": "conversation-1",
                "title": "Imported graph",
                "create_time": 1_700_000_000.25,
                "update_time": 1_700_000_100.5,
                "current_node": "node-2",
                "mapping": {
                    "root": {
                        "id": "root",
                        "parent": None,
                        "children": ["node-1"],
                        "message": None,
                    },
                    "node-1": {
                        "id": "node-1",
                        "parent": "root",
                        "children": ["node-2", "node-3"],
                        "message": {
                            "id": "message-1",
                            "author": {"role": "user"},
                            "create_time": 1_700_000_001.5,
                            "content": {
                                "content_type": "text",
                                "parts": ["question marker"],
                            },
                            "metadata": {},
                        },
                    },
                    "node-2": {
                        "id": "node-2",
                        "parent": "node-1",
                        "children": [],
                        "message": {
                            "id": "message-2",
                            "author": {"role": "assistant"},
                            "create_time": 1_700_000_002.75,
                            "content": {
                                "content_type": "text",
                                "parts": ["canonical answer"],
                            },
                            "metadata": {
                                "model_slug": "gpt-example",
                                "citations": [
                                    {"url": "https://example.test/source", "title": "Source"}
                                ],
                                "attachments": [
                                    {"id": "attachment-1", "name": "note.txt"}
                                ],
                            },
                        },
                    },
                    "node-3": {
                        "id": "node-3",
                        "parent": "node-1",
                        "children": [],
                        "message": {
                            "id": "message-3",
                            "author": {"role": "assistant"},
                            "create_time": 1_700_000_003,
                            "content": {"content_type": "text", "parts": [marker]},
                            "metadata": {"model_slug": "gpt-branch"},
                        },
                    },
                },
            }
        ],
        ensure_ascii=False,
    ).encode()


def _deepseek_export() -> bytes:
    return json.dumps(
        [
            {
                "id": "deepseek-conversation",
                "title": "DeepSeek import",
                "inserted_at": "2026-01-02T03:04:05+00:00",
                "updated_at": "2026-01-02T03:05:05+00:00",
                "mapping": {
                    "request": {
                        "parent": None,
                        "children": ["response"],
                        "message": {
                            "id": "deepseek-request",
                            "inserted_at": "2026-01-02T03:04:06+00:00",
                            "fragments": [{"type": "REQUEST", "content": "request text"}],
                        },
                    },
                    "response": {
                        "parent": "request",
                        "children": [],
                        "message": {
                            "id": "deepseek-response",
                            "inserted_at": "2026-01-02T03:04:07+00:00",
                            "model": "deepseek-example",
                            "fragments": [
                                {"type": "THINK", "content": "private reasoning record"},
                                {
                                    "type": "SEARCH",
                                    "results": [{"url": "https://example.test/result"}],
                                },
                                {"type": "RESPONSE", "content": "DeepSeek answer marker"},
                            ],
                        },
                    },
                },
            }
        ]
    ).encode()


def _service(tmp_path):
    repository = SQLiteRepository(tmp_path / "conversation-imports.db")
    repository.init()
    service = ConversationImportService(
        registry=ConversationImportRegistry.builtins(),
        repository_provider=lambda: repository,
    )
    context = RequestContext(
        request_id="conversation-import-test",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    return repository, service, context


def _counts(repository: SQLiteRepository) -> tuple[int, int, int, int]:
    with repository._connect() as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "artifacts",
                "conversation_import_batches",
                "conversation_import_conversations",
                "conversation_import_messages",
            )
        )


def test_preview_is_side_effect_free_and_commit_preserves_full_graph(tmp_path) -> None:
    repository, service, context = _service(tmp_path)
    source = _chatgpt_export()

    preview = service.preview(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversations.json",
        source_bytes=source,
    )

    assert preview["admission_state"] == "candidate"
    assert preview["conversation_count"] == 1
    assert preview["message_count"] == 3
    assert preview["sample"][0]["canonical_message_count"] == 2
    assert _counts(repository) == (0, 0, 0, 0)

    batch = service.commit(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversations.json",
        source_bytes=source,
        expected_preview_hash=preview["preview_hash"],
    )

    assert batch["status"] == "committed"
    assert batch["admission_state"] == "candidate"
    assert batch["requested_by"] == "user-a"
    assert batch["deduplicated"] is False
    assert _counts(repository) == (1, 1, 1, 3)
    artifact = repository.get_artifact("workspace-a", batch["source_artifact_id"])
    assert artifact["content_hash"] == hashlib.sha256(source).hexdigest()
    assert artifact["content_text"].encode() == source
    assert artifact["metadata"]["admission_state"] == "candidate"

    messages = {
        item["external_id"]: item for item in batch["conversations"][0]["messages"]
    }
    assert messages["message-1"]["parent_external_id"] is None
    assert messages["message-2"]["parent_external_id"] == "message-1"
    assert messages["message-3"]["parent_external_id"] == "message-1"
    assert messages["message-1"]["is_canonical"] is True
    assert messages["message-2"]["is_canonical"] is True
    assert messages["message-3"]["is_canonical"] is False
    assert messages["message-2"]["model"] == "gpt-example"
    assert messages["message-2"]["citations"][0]["title"] == "Source"
    assert messages["message-2"]["attachments"][0]["id"] == "attachment-1"
    assert messages["message-2"]["original_created_at"] == "1700000002.75"
    assert messages["message-2"]["normalized_created_at"].endswith("+00:00")

    hits = repository.search_memory("workspace-a", "branch-only-marker", 20)
    imported = next(item for item in hits if item["kind"] == "conversation_message")
    assert imported["import_batch_id"] == batch["id"]
    assert imported["conversation_id"] == batch["conversations"][0]["id"]
    assert imported["artifact_id"] == batch["source_artifact_id"]

    assert QualificationService(
        repository_provider=lambda: repository
    ).qualified_search(context, "branch-only-marker", "math.formal.v1") == []
    with repository._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM qualification_receipts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM current_use_bindings").fetchone()[0] == 0


def test_preview_hash_is_required_and_duplicate_source_is_idempotent(tmp_path) -> None:
    repository, service, context = _service(tmp_path)
    source = _chatgpt_export("idempotency marker")
    preview = service.preview(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversation.json",
        source_bytes=source,
    )
    with pytest.raises(InvalidConversationImportError):
        service.commit(
            context,
            importer_id="chatgpt.export.v1",
            source_name="conversation.json",
            source_bytes=source,
            expected_preview_hash="0" * 64,
        )
    assert _counts(repository) == (0, 0, 0, 0)

    first = service.commit(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversation.json",
        source_bytes=source,
        expected_preview_hash=preview["preview_hash"],
    )
    second = service.commit(
        context,
        importer_id="chatgpt.export.v1",
        source_name="renamed.json",
        source_bytes=source,
        expected_preview_hash=preview["preview_hash"],
    )
    assert second["id"] == first["id"]
    assert second["deduplicated"] is True
    assert _counts(repository) == (1, 1, 1, 3)


def test_atomic_failure_rolls_back_source_artifact_and_batch(tmp_path) -> None:
    repository, service, _context = _service(tmp_path)
    source = _chatgpt_export("atomic marker")
    normalized = service._normalize(
        "chatgpt.export.v1", "conversation.json", source
    )
    normalized["conversations"].append(dict(normalized["conversations"][0]))
    normalized["conversation_count"] = 2
    artifact_id = normalized["source_artifact_id"]
    with pytest.raises(sqlite3.IntegrityError):
        repository.create_conversation_import(
            "workspace-a",
            {
                **normalized,
                "requested_by": "user-a",
                "artifact": {
                    "id": artifact_id,
                    "series_id": artifact_id,
                    "version": 1,
                    "run_id": None,
                    "step_id": None,
                    "name": normalized["source_name"],
                    "kind": "json",
                    "media_type": "application/json",
                    "content_text": normalized["source_text"],
                    "uri": None,
                    "content_hash": normalized["source_content_hash"],
                    "size_bytes": len(source),
                    "metadata": {"artifact_role": "conversation_import_source"},
                },
            },
        )
    assert _counts(repository) == (0, 0, 0, 0)


def test_import_ledger_is_immutable_and_workspace_scoped(tmp_path) -> None:
    repository, service, context = _service(tmp_path)
    source = _chatgpt_export("immutable marker")
    preview = service.preview(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversation.json",
        source_bytes=source,
    )
    batch = service.commit(
        context,
        importer_id="chatgpt.export.v1",
        source_name="conversation.json",
        source_bytes=source,
        expected_preview_hash=preview["preview_hash"],
    )
    with repository._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE conversation_import_batches SET status = 'committed' WHERE id = ?",
            (batch["id"],),
        )
    assert repository.get_conversation_import("workspace-b", batch["id"]) is None
    assert repository.search_memory("workspace-b", "immutable marker", 20) == []


def test_deepseek_adapter_preserves_fragments_search_results_and_times(tmp_path) -> None:
    repository, service, context = _service(tmp_path)
    source = _deepseek_export()
    preview = service.preview(
        context,
        importer_id="deepseek.export.v1",
        source_name="deepseek.json",
        source_bytes=source,
    )
    batch = service.commit(
        context,
        importer_id="deepseek.export.v1",
        source_name="deepseek.json",
        source_bytes=source,
        expected_preview_hash=preview["preview_hash"],
    )
    response = next(
        item
        for item in batch["conversations"][0]["messages"]
        if item["external_id"] == "deepseek-response"
    )
    assert "<think>private reasoning record</think>" in response["content"]
    assert response["citations"] == [{"url": "https://example.test/result"}]
    assert response["original_created_at"] == "2026-01-02T03:04:07+00:00"
    assert response["model"] == "deepseek-example"
    assert any(
        item["kind"] == "conversation_message"
        for item in repository.search_memory("workspace-a", "answer marker", 20)
    )


def test_registry_rejects_unknown_importer_and_ambiguous_json(tmp_path) -> None:
    _repository, service, context = _service(tmp_path)
    assert {item["importer_id"] for item in service.list_importers()} == {
        "chatgpt.export.v1",
        "deepseek.export.v1",
    }
    with pytest.raises(InvalidConversationImportError):
        service.preview(
            context,
            importer_id="unknown.v1",
            source_name="unknown.json",
            source_bytes=b"[]",
        )
    with pytest.raises(InvalidConversationImportError):
        service.preview(
            context,
            importer_id="chatgpt.export.v1",
            source_name="duplicate-key.json",
            source_bytes=b'[{"id":"one","id":"two","mapping":{}}]',
        )


def test_http_preview_commit_and_read_surface(tmp_path, monkeypatch) -> None:
    repository, service, _context = _service(tmp_path)
    app = FastAPI()
    app.include_router(
        import_api.create_conversation_import_router(
            import_service=service,
            request_context_factory=lambda request, tenant: RequestContext(
                request_id="http-import",
                workspace_id=tenant.id,
                principal_id="admin-a",
            ),
        )
    )
    app.dependency_overrides[current_admin_user] = lambda: {
        "id": "admin-a",
        "tenant_id": "workspace-a",
        "role": "admin",
    }
    app.dependency_overrides[current_tenant] = lambda: Tenant(
        "workspace-a", "Workspace A"
    )
    monkeypatch.setattr(import_api, "get_repository", lambda: repository)
    source = _chatgpt_export("http import marker")

    with TestClient(app) as client:
        importers = client.get("/api/conversation-importers")
        assert importers.status_code == 200
        preview = client.post(
            "/api/conversation-imports/preview",
            data={"importer_id": "chatgpt.export.v1"},
            files={"file": ("conversations.json", source, "application/json")},
        )
        assert preview.status_code == 200
        committed = client.post(
            "/api/conversation-imports",
            data={
                "importer_id": "chatgpt.export.v1",
                "expected_preview_hash": preview.json()["preview_hash"],
            },
            files={"file": ("conversations.json", source, "application/json")},
        )
        assert committed.status_code == 201
        batch_id = committed.json()["id"]
        assert committed.json()["conversations"][0]["message_count"] == 3
        assert client.get("/api/conversation-imports").json()[0]["id"] == batch_id
        detail = client.get(
            f"/api/conversation-imports/{batch_id}?include_messages=true"
        )
        assert detail.status_code == 200
        assert len(detail.json()["conversations"][0]["messages"]) == 3
