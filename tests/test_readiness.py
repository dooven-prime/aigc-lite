from app.services.readiness import ReadinessService


class ReadyRepository:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def schema_revision(self) -> str:
        return self.revision


class BrokenRepository:
    def schema_revision(self) -> str:
        raise RuntimeError("sensitive database error")


def _service(repository, *, enabled: bool = True, running: bool = True):
    return ReadinessService(
        repository_provider=lambda: repository,
        schema_head_provider=lambda: "0013_model_credential_binding",
        scheduler_enabled=lambda: enabled,
        scheduler_running=lambda: running,
    )


def test_readiness_requires_database_head_and_running_scheduler() -> None:
    ready, payload = _service(
        ReadyRepository("0013_model_credential_binding")
    ).snapshot()

    assert ready is True
    assert payload["status"] == "ready"
    assert payload["checks"]["database"] == {"status": "ok"}
    assert payload["checks"]["migration"]["status"] == "ok"
    assert payload["checks"]["scheduler"] == {"status": "ok"}


def test_readiness_accepts_explicitly_disabled_scheduler() -> None:
    ready, payload = _service(
        ReadyRepository("0013_model_credential_binding"),
        enabled=False,
        running=False,
    ).snapshot()

    assert ready is True
    assert payload["checks"]["scheduler"] == {"status": "disabled"}


def test_readiness_fails_closed_without_leaking_database_error() -> None:
    ready, payload = _service(BrokenRepository()).snapshot()

    assert ready is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["database"] == {"status": "unavailable"}
    assert payload["checks"]["migration"]["status"] == "unavailable"
    assert "sensitive" not in str(payload)


def test_readiness_rejects_stale_migration_or_stopped_scheduler() -> None:
    migration_ready, migration_payload = _service(
        ReadyRepository("0012_qualification_plane")
    ).snapshot()
    scheduler_ready, scheduler_payload = _service(
        ReadyRepository("0013_model_credential_binding"), running=False
    ).snapshot()

    assert migration_ready is False
    assert migration_payload["checks"]["migration"]["status"] == "stale"
    assert scheduler_ready is False
    assert scheduler_payload["checks"]["scheduler"]["status"] == "unavailable"
