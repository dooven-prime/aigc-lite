"""Replay a database written by the tagged v0.7.0 application itself.

The synthetic theorem/certificate is a migration fixture, not a mathematical
verification claim. No SQL inserts create business rows in this test: a child
process imports the published application code and calls its services. The
old database and its hash-stamped backup remain outside the repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.contracts import RequestContext
from app.repository import SQLiteRepository
from app.services.qualification import QualificationService

TAG_COMMIT = "834ebff7ac17f7825d8afab0c7e277f9185bd853"
OLD_HEAD = "0025_invocation_timeout"
NEW_HEAD = "0029_shared_attempt_invalidation"
WORKSPACE = "runtime-v07-upgrade"


_V0_7_RUNTIME_WRITER = r'''
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import app
from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.authority import ADMIN_AUTHORITY_SCOPES, AuthorityContext
from app.core.contracts import RequestContext
from app.core.kernel_verification import KERNEL_EXECUTION_CONTRACT_VERSION
from app.core.qualification import (
    AuthorizationGrantDraft, KnowledgeAdmissionDraft, MathTheoremCandidateDraft,
    ValidationModality,
)
from app.core.research import VerificationAttemptDraft, VerificationKind, VerificationOutcome
from app.profiles.math_theorem import KERNEL_CERTIFICATE_VERSION, PROFILE_ID
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService

root = Path.cwd().resolve()
assert Path(app.__file__).resolve().is_relative_to(root)
assert app.__version__ == "0.7.0"
repository = SQLiteRepository(Path(sys.argv[1]))
repository.init()
workspace = "runtime-v07-upgrade"
repository.create_tenant("Tagged runtime upgrade fixture", workspace)
user = repository.create_user(
    workspace, "v07-upgrade@example.test", "Tagged runtime administrator",
    "synthetic-unused-password-hash", role="admin",
)
session_hash = uuid4().hex + uuid4().hex
repository.save_auth_session(
    session_hash, user["id"], workspace,
    (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
)
context = RequestContext("runtime-v07-writer", workspace, user["id"])
authority = AuthorityContext(
    workspace_id=workspace, principal_id=user["id"],
    session_hash=session_hash, scopes=ADMIN_AUTHORITY_SCOPES,
)
provider = lambda: repository
artifacts = ArtifactService(repository_provider=provider)
evidence = EvidenceService(provider)
research = ResearchRegistryService(
    provider, artifact_service=artifacts, evidence_service=evidence,
)
qualification = QualificationService(
    provider, artifact_service=artifacts, evidence_service=evidence,
)
claim = qualification.register_math_theorem(
    context,
    MathTheoremCandidateDraft(
        claim_key="V07-RUNTIME-FIXTURE",
        name="Tagged runtime theorem fixture",
        statement="For every integer n, n = n.", scope="Integers only.",
    ),
)["claim"]
proof = artifacts.create_artifact(
    context, ArtifactDraft(
        name="fixture.lean", kind=ArtifactKind.TEXT, media_type="text/plain",
        content_text="theorem fixture (n : Int) : n = n := rfl",
        metadata={"role": "formal_proof", "language": "Lean"},
    ),
)
certificate_payload = {
    "contract_version": KERNEL_CERTIFICATE_VERSION,
    "execution_contract_version": KERNEL_EXECUTION_CONTRACT_VERSION,
    "claim_revision_id": claim["id"],
    "claim_semantic_hash": claim["semantic_hash"],
    "statement_hash": claim["semantic_hash"],
    "proof_artifact_id": proof["id"],
    "proof_artifact_hash": proof["content_hash"],
    "checker": {
        "backend": "lean4", "name": "lean-kernel", "version": "4.23.0",
        "executable_hash": "a" * 64, "toolchain_hash": "a" * 64,
    },
    "status": "passed", "axioms": [], "sorry_present": False,
    "dependencies": [],
    "invocation": {"command": ["lean", "Main.lean"], "exit_code": 0},
    "isolation": {"shell": False, "request_controls_command": False},
}
certificate = artifacts.create_artifact(
    context, ArtifactDraft(
        name="fixture.kernel-certificate.json", kind=ArtifactKind.JSON,
        media_type="application/json",
        content_text=json.dumps(certificate_payload, sort_keys=True),
        metadata={"role": "kernel_certificate"},
    ),
)
kernel_context = RequestContext(
    "runtime-v07-kernel-record", workspace, "system:verifier:lean-kernel",
)
attempt = research.record_verification_attempt(
    kernel_context, claim["id"], VerificationAttemptDraft(
        kind=VerificationKind.CALCULATION,
        outcome=VerificationOutcome.PASSED,
        validation_modality=ValidationModality.KERNEL_CHECK,
        method="Synthetic migration fixture through tagged service",
        scope="Exact frozen fixture revision", input_digest=claim["semantic_hash"],
        output_digest=certificate["content_hash"],
        artifact_ids=(proof["id"], certificate["id"]),
    ),
)
research.record_verification_attempt(
    context, claim["id"], VerificationAttemptDraft(
        kind=VerificationKind.REVIEW,
        outcome=VerificationOutcome.PASSED,
        validation_modality=ValidationModality.EXPERT_REVIEW,
        method="Synthetic fixture statement alignment",
        scope="No real mathematical review is claimed",
        input_digest=claim["semantic_hash"], output_digest=proof["content_hash"],
        artifact_ids=(proof["id"],),
    ),
)
evaluation = qualification.evaluate(context, claim["id"], PROFILE_ID)
assert evaluation["evaluation"]["verdict"] == "ADMITTED", evaluation["evaluation"]
receipt = evaluation["qualification_receipt"]
assert receipt is not None
admission = qualification.admit_knowledge(
    context, KnowledgeAdmissionDraft(
        qualification_receipt_id=receipt["id"],
        admission_policy_id="knowledge.default.v1",
        rationale="Synthetic old-runtime migration acceptance fixture.",
    ), authority=authority,
)
assert admission["current_use_binding"]["state"] == "current"
grant = qualification.create_authorization(
    context, "runtime-v07-actor", AuthorizationGrantDraft(
        qualification_receipt_id=receipt["id"],
        action="publish", target="knowledge/runtime-v07-fixture",
        scope={"claim_revision_id": claim["id"]}, max_calls=1,
    ), authority=authority,
)
assert grant["state"] == "active" and grant["calls_used"] == 0
print(json.dumps({
    "runtime_version": app.__version__, "runtime_source": str(Path(app.__file__).resolve()),
    "workspace_id": workspace, "claim_id": claim["id"],
    "attempt_id": attempt["id"], "receipt_id": receipt["id"],
    "receipt_hash": receipt["receipt_hash"],
    "admission_id": admission["knowledge_admission_receipt"]["id"],
    "binding_id": admission["current_use_binding"]["id"],
    "grant_id": grant["id"],
}, sort_keys=True))
'''


def _published_runtime(tmp_path: Path) -> Path:
    checkout = Path(__file__).resolve().parents[1]
    tag = subprocess.run(
        ["git", "rev-parse", "--verify", "v0.7.0^{commit}"],
        cwd=checkout, capture_output=True, text=True, check=True,
    )
    assert tag.stdout.strip() == TAG_COMMIT
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "v0.7.0", "app"],
        cwd=checkout, capture_output=True, check=True,
    )
    root = tmp_path / "tagged-v0.7.0-runtime"
    with tarfile.open(fileobj=BytesIO(archive.stdout), mode="r:") as source:
        for member in source:
            if member.isdir():
                continue
            assert member.isfile() and member.name.startswith("app/")
            target = (root / member.name).resolve()
            assert target.is_relative_to(root.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = source.extractfile(member)
            assert payload is not None
            target.write_bytes(payload.read())
    return root


def _config(database_path: Path) -> Config:
    checkout = Path(__file__).resolve().parents[1]
    config = Config(str(checkout / "app" / "alembic.ini"))
    config.set_main_option("script_location", str(checkout / "app" / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _snapshot(database_path: Path) -> dict[str, list[tuple]]:
    tables = (
        "research_claim_revisions", "research_verification_attempts",
        "qualification_evaluations", "qualification_receipts",
        "knowledge_admission_receipts", "current_use_bindings",
        "authorization_grants", "enforcement_dispatches",
    )
    with sqlite3.connect(database_path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in tables
        }


def test_tagged_v0_7_runtime_written_history_survives_copy_upgrade(tmp_path: Path) -> None:
    root = _published_runtime(tmp_path)
    original = tmp_path / "actual-v0.7-runtime.db"
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTHONNOUSERSITE": "1"}
    created = subprocess.run(
        [sys.executable, "-c", _V0_7_RUNTIME_WRITER, str(original)],
        cwd=root, env=environment, capture_output=True, text=True, check=True,
    )
    identity = json.loads(created.stdout.strip())
    assert identity["runtime_version"] == "0.7.0"
    assert Path(identity["runtime_source"]).is_relative_to(root)
    assert original.exists()
    before = _snapshot(original)
    assert [len(before[table]) for table in (
        "qualification_receipts", "knowledge_admission_receipts",
        "current_use_bindings", "authorization_grants",
    )] == [1, 1, 1, 1]

    # The original remains a hash-stamped, closed source image. Upgrade only a copy.
    backup = tmp_path / "isolated-v0.7-backup.db"
    upgraded = tmp_path / "v0.8-upgrade-copy.db"
    shutil.copyfile(original, backup)
    shutil.copyfile(backup, upgraded)
    source_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    backup_stamp = tmp_path / "isolated-v0.7-backup.sha256"
    backup_stamp.write_text(f"{source_hash}  {backup.name}\n", encoding="ascii")
    assert backup_stamp.read_text(encoding="ascii") == (
        f"{source_hash}  {backup.name}\n"
    )
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(upgraded.read_bytes()).hexdigest() == source_hash
    with sqlite3.connect(original) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == OLD_HEAD

    command.upgrade(_config(upgraded), NEW_HEAD)
    assert _snapshot(upgraded) == before
    assert _snapshot(backup) == before
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == source_hash
    with sqlite3.connect(upgraded) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == NEW_HEAD
        selection = db.execute(
            "SELECT attempt_id, selection_version FROM "
            "qualification_receipt_attempt_selections WHERE qualification_receipt_id = ?",
            (identity["receipt_id"],),
        ).fetchall()
    assert selection == [
        (identity["attempt_id"], "statement-identity-kernel-attempt.v1")
    ]
    repository = SQLiteRepository(upgraded)
    receipt = repository.get_qualification_receipt(WORKSPACE, identity["receipt_id"])
    assert receipt is not None and receipt["receipt_hash"] == identity["receipt_hash"]
    binding = repository.get_current_use_binding(
        WORKSPACE, identity["claim_id"], "math.formal.v1"
    )
    assert binding is not None and binding["id"] == identity["binding_id"]
    assert binding["state"] == "current"
    grant = next(
        item for item in repository.list_authorization_grants(WORKSPACE)
        if item["id"] == identity["grant_id"]
    )
    assert grant["state"] == "active" and grant["calls_used"] == 0
    status = QualificationService(lambda: repository).claim_status(
        RequestContext("upgraded-runtime-read", WORKSPACE, "runtime-v07-actor"),
        identity["claim_id"],
    )
    assert status["current_use_bindings"][0]["state"] == "current"
    consumed = repository.consume_authorization_grant(
        WORKSPACE, "runtime-v07-actor", "publish",
        "knowledge/runtime-v07-fixture", datetime.now(UTC).isoformat(),
    )
    assert consumed is not None and consumed["id"] == identity["grant_id"]
    assert consumed["calls_used"] == 1
    assert _snapshot(backup) == before
