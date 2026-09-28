import sqlite3

from fastapi.testclient import TestClient

from app import database, main


def _register(client: TestClient, email: str, name: str) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "long-enough-password",
            "name": name,
        },
    )
    assert response.status_code == 200
    value = response.json()
    return value, {"Authorization": f"Bearer {value['access_token']}"}


def test_admin_schedule_api_controls_persistent_tasks(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "scheduler-api.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    notified: list[tuple[str, str]] = []
    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "allow_signup", True)
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)
    monkeypatch.setattr(
        main.timewheel_scheduler,
        "notify",
        lambda task: notified.append((task.id, task.status.value)),
    )

    with TestClient(main.app) as client:
        registration, headers = _register(
            client, "scheduler-admin@example.com", "Scheduler Admin"
        )
        created = client.post(
            "/api/schedules",
            headers=headers,
            json={
                "name": "Daily workspace summary",
                "target": "agent.chat",
                "payload": {
                    "prompt": "Summarize the workspace",
                    "system": "Be concise.",
                },
                "kind": "interval",
                "run_at": "2030-01-02T03:04:05+08:00",
                "interval_seconds": 86400,
            },
        )

        assert created.status_code == 201
        task = created.json()
        task_id = task["id"]
        assert task["workspace_id"] == registration["tenant_id"]
        assert task["status"] == "scheduled"
        assert task["next_run_at"] == "2030-01-01T19:04:05+00:00"
        assert task["payload"] == {
            "prompt": "Summarize the workspace",
            "system": "Be concise.",
        }

        listed = client.get("/api/schedules", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [task_id]
        assert client.get(
            f"/api/schedules/{task_id}", headers=headers
        ).json() == task

        paused = client.post(
            f"/api/schedules/{task_id}/pause", headers=headers
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        resumed = client.post(
            f"/api/schedules/{task_id}/resume", headers=headers
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "scheduled"

        cancelled = client.post(
            f"/api/schedules/{task_id}/cancel", headers=headers
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        conflict = client.post(
            f"/api/schedules/{task_id}/cancel", headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "schedule_not_active"

        assert notified == [
            (task_id, "scheduled"),
            (task_id, "paused"),
            (task_id, "scheduled"),
            (task_id, "cancelled"),
        ]
        audit = client.get("/api/audit", headers=headers).json()
        actions = {item["action"] for item in audit}
        assert {
            "schedule.create",
            "schedule.pause",
            "schedule.resume",
            "schedule.cancel",
        } <= actions
        explicit = next(
            item for item in audit if item["action"] == "schedule.create"
        )
        assert explicit["metadata"] == {
            "task_id": task_id,
            "target": "agent.chat",
            "kind": "interval",
            "status": "scheduled",
        }


def test_schedule_api_is_admin_only_tenant_scoped_and_validates_targets(
    tmp_path, monkeypatch
) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "scheduler-security.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "allow_signup", True)
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)
    monkeypatch.setattr(
        main.settings, "http_poll_allowed_hosts", "status.example.test"
    )

    with TestClient(main.app) as client:
        alpha, alpha_headers = _register(client, "alpha@example.com", "Alpha")
        task = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "One-time summary",
                "payload": {"prompt": "Summarize pending work"},
                "run_at": "2030-01-01T00:00:00Z",
            },
        ).json()

        _, beta_headers = _register(client, "beta@example.com", "Beta")
        assert client.get(
            f"/api/schedules/{task['id']}", headers=beta_headers
        ).status_code == 404
        assert client.get("/api/schedules", headers=beta_headers).json() == []

        member = client.post(
            f"/api/admin/tenants/{alpha['tenant_id']}/users",
            headers=alpha_headers,
            json={
                "email": "member@example.com",
                "password": "long-enough-password",
                "name": "Member",
                "role": "member",
            },
        )
        assert member.status_code == 200
        member_token = client.post(
            "/api/auth/login",
            json={
                "email": "member@example.com",
                "password": "long-enough-password",
            },
        ).json()["access_token"]
        member_headers = {"Authorization": f"Bearer {member_token}"}
        assert client.get(
            "/api/schedules", headers=member_headers
        ).status_code == 403

        unknown = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "Unsafe import",
                "target": "python.import",
                "payload": {"prompt": "hello"},
                "run_at": "2030-01-01T00:00:00Z",
            },
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "invalid_schedule"

        invalid_payload = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "Missing prompt",
                "target": "agent.chat",
                "payload": {"model": "demo"},
                "run_at": "2030-01-01T00:00:00Z",
            },
        )
        assert invalid_payload.status_code == 422
        assert invalid_payload.json()["error"]["code"] == "invalid_schedule"

        mcp_probe = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "MCP health poll",
                "target": "mcp.probe",
                "payload": {"server_id": "server-1"},
                "kind": "interval",
                "run_at": "2030-01-01T00:00:00Z",
                "interval_seconds": 300,
            },
        )
        assert mcp_probe.status_code == 201
        assert mcp_probe.json()["target"] == "mcp.probe"

        tool_call = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "Workspace status poll",
                "target": "tool.call",
                "payload": {"name": "workspace_status", "arguments": {}},
                "kind": "interval",
                "run_at": "2030-01-01T00:00:00Z",
                "interval_seconds": 60,
            },
        )
        assert tool_call.status_code == 201
        assert tool_call.json()["target"] == "tool.call"

        http_poll = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "Public endpoint poll",
                "target": "http.poll",
                "payload": {
                    "url": "https://status.example.test/health",
                    "expected_status": 200,
                },
                "kind": "interval",
                "run_at": "2030-01-01T00:00:00Z",
                "interval_seconds": 60,
            },
        )
        assert http_poll.status_code == 201
        assert http_poll.json()["target"] == "http.poll"

        disallowed_http_poll = client.post(
            "/api/schedules",
            headers=alpha_headers,
            json={
                "name": "Blocked internal endpoint",
                "target": "http.poll",
                "payload": {"url": "http://127.0.0.1/private"},
                "run_at": "2030-01-01T00:00:00Z",
            },
        )
        assert disallowed_http_poll.status_code == 422
        assert (
            disallowed_http_poll.json()["error"]["code"]
            == "invalid_schedule"
        )


def test_schedule_api_expands_daily_and_weekly_cadence(tmp_path, monkeypatch) -> None:
    def connect_to_test_db() -> sqlite3.Connection:
        connection = sqlite3.connect(tmp_path / "scheduler-cadence.db", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(database, "_connect", connect_to_test_db)
    monkeypatch.setattr(main.settings, "allow_signup", True)
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)

    with TestClient(main.app) as client:
        _, headers = _register(client, "cadence@example.com", "Cadence Admin")
        for cadence, seconds in (("daily", 86400), ("weekly", 604800)):
            response = client.post(
                "/api/schedules",
                headers=headers,
                json={
                    "name": f"{cadence} summary",
                    "payload": {"prompt": f"Run the {cadence} summary"},
                    "cadence": cadence,
                    "run_at": "2030-01-01T00:00:00Z",
                },
            )
            assert response.status_code == 201
            assert response.json()["kind"] == "interval"
            assert response.json()["interval_seconds"] == seconds
