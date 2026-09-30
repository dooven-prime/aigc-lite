import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.adapters.kernel_verification import (
    CoqKernelVerifierBackend,
    KernelVerifierRegistry,
    LeanKernelVerifierBackend,
)
from app.core.contracts import RequestContext
from app.core.kernel_verification import (
    KernelBackendKind,
    KernelExecutionStatus,
    KernelVerificationDraft,
)
from app.core.qualification import MathTheoremCandidateDraft
from app.profiles.math_theorem import PROFILE_ID
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.evidence import EvidenceService
from app.services.kernel_verification import KernelVerificationService
from app.services.qualification import QualificationService
from app.services.research_registry import ResearchRegistryService


def _fake_checker(tmp_path: Path) -> Path:
    checker = tmp_path / "fake_kernel.py"
    checker.write_text(
        """
import json
import pathlib
import sys
import time

if "--version" in sys.argv:
    print("Fake proof kernel 1.0")
    raise SystemExit(0)

source = pathlib.Path(sys.argv[-1]).read_text(encoding="utf-8")
if "SLOW_CHECK" in source:
    time.sleep(2)
if sys.argv[-1].endswith(".lean"):
    name = source.rsplit("#print axioms ", 1)[-1].strip()
    if "sorry" in source:
        print(json.dumps({"kind": "hasSorry", "data": "declaration uses `sorry`"}))
        print(json.dumps({"kind": "info", "data": f"'{name}' depends on axioms: [sorryAx]"}))
    elif "axiom bad" in source:
        print(json.dumps({"kind": "info", "data": f"'{name}' depends on axioms: [bad]"}))
    else:
        print(json.dumps({"kind": "info", "data": f"'{name}' does not depend on any axioms"}))
elif "Axiom bad" in source:
    print("Axioms:")
    print("bad : False")
else:
    print("Closed under the global context")
""".strip(),
        encoding="utf-8",
    )
    return checker


def _services(tmp_path: Path, *, timeout: float = 5) -> tuple:
    repository = SQLiteRepository(tmp_path / "kernel.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    evidence = EvidenceService(provider)
    research = ResearchRegistryService(
        provider,
        artifact_service=artifacts,
        evidence_service=evidence,
    )
    qualification = QualificationService(
        provider,
        artifact_service=artifacts,
        evidence_service=evidence,
    )
    checker = _fake_checker(tmp_path)
    backend = LeanKernelVerifierBackend(
        sys.executable,
        fixed_args=(str(checker),),
        timeout_seconds=timeout,
    )
    kernel = KernelVerificationService(
        registry=KernelVerifierRegistry([backend]),
        repository_provider=provider,
        artifact_service=artifacts,
        research_registry_service=research,
    )
    return repository, qualification, kernel


def _claim(qualification: QualificationService, context: RequestContext) -> dict:
    return qualification.register_math_theorem(
        context,
        MathTheoremCandidateDraft(
            claim_key="THM-KERNEL",
            name="Kernel checked identity",
            statement="For every natural n, n + 0 = n.",
            scope="Natural numbers in the configured proof assistant.",
        ),
    )["claim"]


@pytest.mark.asyncio
async def test_lean_kernel_service_records_server_derived_evidence(tmp_path) -> None:
    repository, qualification, kernel = _services(tmp_path)
    context = RequestContext("kernel-request", "workspace-a", "admin-a")
    claim = _claim(qualification, context)

    result = await kernel.verify(
        context,
        claim["id"],
        KernelVerificationDraft(
            backend=KernelBackendKind.LEAN4,
            declaration_name="add_zero_demo",
            source=(
                "theorem add_zero_demo (n : Nat) : n + 0 = n := by\n"
                "  exact Nat.add_zero n"
            ),
        ),
    )

    assert result["status"] == KernelExecutionStatus.PASSED
    assert result["kernel_evidence_eligible"] is True
    assert result["qualification_granted"] is False
    assert result["run"]["status"] == "succeeded"
    assert result["run"]["selected_model"] == "kernel/lean4"
    assert result["step"]["name"] == "kernel.verify.lean4"
    assert result["proof_artifact"]["metadata"]["role"] == "formal_proof"
    certificate = json.loads(result["certificate_artifact"]["content_text"])
    assert certificate["status"] == "passed"
    assert certificate["axioms"] == []
    assert certificate["sorry_present"] is False
    assert certificate["proof_artifact_hash"] == result["proof_artifact"][
        "content_hash"
    ]
    assert certificate["invocation"]["command"][0] == "lean"
    assert str(tmp_path) not in json.dumps(certificate)
    attempt = result["verification_attempt"]
    assert attempt["outcome"] == "passed"
    assert attempt["independent"] is False
    assert attempt["independence"]["qualified"] is False
    assert attempt["independence"]["basis"] == []
    assert attempt["independence"]["verification_properties"] == [
        "orthogonal_non_llm_checker"
    ]
    assert attempt["verifier_lineage"]["model_route"] is None
    assert attempt["verifier_lineage"]["principal_id"] == (
        "system:verifier:lean4-kernel"
    )
    assert attempt["verifier_lineage"]["toolchain_hash"] == certificate["checker"][
        "toolchain_hash"
    ]
    assert repository.get_run(context.workspace_id, result["run"]["id"]) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_axioms", "sorry_present"),
    [
        (
            "axiom bad : False\ntheorem bad_demo : False := bad",
            ["bad"],
            False,
        ),
        (
            "theorem bad_demo : False := by\n  sorry",
            ["sorryAx"],
            True,
        ),
    ],
)
async def test_lean_axioms_and_sorry_fail_closed(
    tmp_path, source, expected_axioms, sorry_present
) -> None:
    _repository, qualification, kernel = _services(tmp_path)
    context = RequestContext("kernel-reject", "workspace-a", "admin-a")
    claim = _claim(qualification, context)

    result = await kernel.verify(
        context,
        claim["id"],
        KernelVerificationDraft(
            backend=KernelBackendKind.LEAN4,
            declaration_name="bad_demo",
            source=source,
        ),
    )

    certificate = json.loads(result["certificate_artifact"]["content_text"])
    assert result["status"] == "failed"
    assert result["kernel_evidence_eligible"] is False
    assert result["qualification_granted"] is False
    assert result["run"]["status"] == "failed"
    assert result["verification_attempt"]["outcome"] == "failed"
    assert certificate["axioms"] == expected_axioms
    assert certificate["sorry_present"] is sorry_present


@pytest.mark.asyncio
async def test_kernel_timeout_is_recorded_as_failed_attempt(tmp_path) -> None:
    _repository, qualification, kernel = _services(tmp_path, timeout=0.05)
    context = RequestContext("kernel-timeout", "workspace-a", "admin-a")
    claim = _claim(qualification, context)

    result = await kernel.verify(
        context,
        claim["id"],
        KernelVerificationDraft(
            backend=KernelBackendKind.LEAN4,
            declaration_name="slow_demo",
            source="-- SLOW_CHECK\ntheorem slow_demo : True := by trivial",
        ),
    )

    assert result["status"] == "timeout"
    assert result["run"]["status"] == "failed"
    assert result["run"]["error_code"] == "kernel_verifier_timeout"
    assert result["verification_attempt"]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_explicit_import_is_disclosed_and_blocks_math_profile(tmp_path) -> None:
    _repository, qualification, kernel = _services(tmp_path)
    context = RequestContext("kernel-import", "workspace-a", "admin-a")
    claim = _claim(qualification, context)

    result = await kernel.verify(
        context,
        claim["id"],
        KernelVerificationDraft(
            backend=KernelBackendKind.LEAN4,
            declaration_name="imported_demo",
            source=(
                "import Mathlib\n"
                "theorem imported_demo (n : Nat) : n + 0 = n := by omega"
            ),
        ),
    )
    certificate = json.loads(result["certificate_artifact"]["content_text"])
    gate = qualification.evaluate(context, claim["id"], PROFILE_ID)

    assert result["status"] == "passed"
    assert certificate["dependencies"] == [
        {"closure": "unbound", "name": "Mathlib"}
    ]
    assert gate["evaluation"]["verdict"] == "BLOCKED"
    assert "unclosed_proof_dependencies" in gate["evaluation"]["blockers"]


@pytest.mark.asyncio
async def test_kernel_process_is_killed_when_caller_is_cancelled(tmp_path) -> None:
    checker = _fake_checker(tmp_path)
    backend = LeanKernelVerifierBackend(
        sys.executable,
        fixed_args=(str(checker),),
        timeout_seconds=10,
    )
    task = asyncio.create_task(
        backend.verify(
            KernelVerificationDraft(
                KernelBackendKind.LEAN4,
                "slow_demo",
                "-- SLOW_CHECK\ntheorem slow_demo : True := by trivial",
            )
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_coq_backend_parses_closed_and_axiomatic_results(tmp_path) -> None:
    checker = _fake_checker(tmp_path)
    backend = CoqKernelVerifierBackend(
        sys.executable,
        fixed_args=(str(checker),),
        timeout_seconds=5,
    )
    closed = await backend.verify(
        KernelVerificationDraft(
            KernelBackendKind.COQ,
            "identity",
            "Theorem identity : forall P : Prop, P -> P. Proof. auto. Qed.",
        )
    )
    axiomatic = await backend.verify(
        KernelVerificationDraft(
            KernelBackendKind.COQ,
            "bad_demo",
            "Axiom bad : False. Theorem bad_demo : False. exact bad. Qed.",
        )
    )

    assert closed.status is KernelExecutionStatus.PASSED
    assert closed.axioms == ()
    assert axiomatic.status is KernelExecutionStatus.FAILED
    assert axiomatic.axioms == ("bad",)
