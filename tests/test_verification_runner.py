import asyncio
import json

import pytest

from app.core.artifacts import ArtifactDraft, ArtifactKind
from app.core.contracts import ChatResult, RequestContext, RunStatus, StepKind, StepStatus
from app.core.errors import (
    InvalidEvidenceError,
    InvalidScheduleError,
    InvalidVerificationResultError,
)
from app.core.research import (
    VerificationAttemptDraft,
    VerificationKind,
    VerificationOutcome,
    VerificationPlanDraft,
)
from app.redaction import redact_record_text
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.assurance import AssuranceBundleService, AssuranceBundleVerifier
from app.services.evidence import EvidenceService
from app.services.research_registry import ResearchRegistryService
from app.services.task_runner import TaskRunner
from app.services.verification_runner import RESULT_CONTRACT_VERSION, VerificationRunner


def _registry() -> bytes:
    return json.dumps(
        {
            "registry_id": "verification-runner-fixture",
            "registry_version": "1.0.0",
            "scope": {
                "as_of_date": "2026-09-28",
                "authority": "research_only",
                "causal_default": "NOT_ESTABLISHED",
            },
            "claim_contract": {
                "required_fields": [
                    "claim_id",
                    "statement",
                    "claim_type",
                    "scope",
                    "source_refs",
                    "calculation_version",
                    "uncertainty_status",
                    "causal_status",
                    "revision_status",
                ],
                "claim_types": ["OBSERVATION"],
            },
            "claims": [
                {
                    "claim_id": "CLM-RUNNER",
                    "statement": "The frozen fixture contains one bounded observation.",
                    "claim_type": "OBSERVATION",
                    "scope": "Fixture only.",
                    "source_refs": [
                        {
                            "source_id": "SRC-FIXTURE",
                            "locator": "fixture.json!claim=CLM-RUNNER",
                            "sha256": "a" * 64,
                        }
                    ],
                    "calculation_version": "fixture-v1",
                    "uncertainty_status": "observed",
                    "causal_status": "NOT_ESTABLISHED",
                    "revision_status": "current",
                }
            ],
        }
    ).encode()


class FakeGateway:
    def __init__(self, repository: SQLiteRepository, content: str) -> None:
        self.repository = repository
        self.content = content

    async def chat(self, command, context) -> ChatResult:
        session = self.repository.create_session(context.workspace_id, "Verification")
        run = self.repository.create_run(
            context.workspace_id,
            session["id"],
            context.request_id,
            command.requested_model,
            command.requested_model or "fixture-model",
        )
        self.repository.append_run_step(
            context.workspace_id,
            run["id"],
            1,
            StepKind.MODEL.value,
            "verification_agent",
            StepStatus.SUCCEEDED.value,
            command.prompt,
            self.content,
            {"fixture": True},
        )
        self.repository.finish_run(
            context.workspace_id, run["id"], RunStatus.SUCCEEDED.value
        )
        return ChatResult(
            content=self.content,
            session_id=session["id"],
            run_id=run["id"],
        )


def _services(tmp_path, content: str):
    repository = SQLiteRepository(tmp_path / "verification-runner.db")
    repository.init()
    provider = lambda: repository  # noqa: E731
    artifacts = ArtifactService(repository_provider=provider)
    research = ResearchRegistryService(
        provider,
        artifact_service=artifacts,
        evidence_service=EvidenceService(provider),
    )
    runner = VerificationRunner(
        gateway_service=FakeGateway(repository, content),
        research_registry_service=research,
        artifact_service=artifacts,
        repository_provider=provider,
    )
    return repository, research, runner


def _claim(research: ResearchRegistryService, context: RequestContext) -> dict:
    imported = research.import_frontier(
        context,
        source_name="Verification runner fixture",
        registry_bytes=_registry(),
    )
    return imported["claims"][0]


def _draft() -> VerificationPlanDraft:
    return VerificationPlanDraft(
        plan_key="source-recheck",
        name="Source recheck",
        kind=VerificationKind.SOURCE_AUDIT,
        method="Inspect the frozen source declaration and report bounded findings.",
        scope="The imported fixture and its declared source reference only.",
        prompt="Check whether the claim is supported within the declared fixture scope.",
        system="Act as a careful verification agent.",
        model="fixture-model",
        auto_promote=True,
    )


def _result_payload() -> dict:
    return {
        "contract_version": RESULT_CONTRACT_VERSION,
        "outcome": "passed",
        "summary": "Verified.",
        "findings": ["A bounded finding."],
        "evidence_refs": [],
        "limitations": [],
    }


def _invalid_result_examples() -> list[str]:
    valid = json.dumps(_result_payload())
    return [
        valid.replace('"outcome": "passed"', '"outcome": "passed", "outcome": "failed"'),
        valid.replace(
            '"summary": "Verified."',
            '"summary": "Verified.", "probe": {"key": 1, "key": 2}',
        ),
        valid.replace('"summary": "Verified."', '"summary": NaN'),
        valid.replace('"summary": "Verified."', '"summary": Infinity'),
        valid.replace('"summary": "Verified."', '"summary": 1e999'),
        valid.replace('"summary": "Verified."', '"summary": ' + "[" * 80 + "0" + "]" * 80),
        json.dumps({**_result_payload(), "summary": "x" * 1_048_576}),
        json.dumps(
            {**_result_payload(), "summary": "😀" * 262_144}, ensure_ascii=False
        ),
        json.dumps({**_result_payload(), "summary": "\ud800"}),
        valid + " trailing prose",
        valid + valid,
        f"```json\n{valid}\n```",
        valid[:-1],
    ]


@pytest.mark.parametrize(
    "content",
    _invalid_result_examples(),
    ids=[
        "duplicate-outcome",
        "nested-duplicate-key",
        "nan",
        "infinity",
        "float-overflow",
        "over-depth",
        "over-byte-limit",
        "multibyte-over-byte-limit",
        "escaped-unpaired-surrogate",
        "trailing-prose",
        "two-documents",
        "markdown-fence",
        "truncated-json",
    ],
)
def test_verification_result_json_boundary_rejects_ambiguous_output(content) -> None:
    with pytest.raises(InvalidVerificationResultError):
        VerificationRunner._parse_result(content)


def test_verification_result_json_boundary_accepts_plain_unicode_and_whitespace() -> None:
    content = json.dumps({**_result_payload(), "summary": "中文与 emoji 😀"}, ensure_ascii=False)
    result = VerificationRunner._parse_result(f"  \n{content}\n  ")
    assert result["summary"] == "中文与 emoji 😀"
    with pytest.raises(InvalidVerificationResultError):
        VerificationRunner._parse_result(
            json.dumps({**_result_payload(), "summary": "\ud800"}, ensure_ascii=False)
        )


def test_verification_result_json_byte_limit_is_exact() -> None:
    empty = json.dumps({**_result_payload(), "summary": ""})
    padding = "x" * (1_048_576 - len(empty.encode("utf-8")))
    content = json.dumps({**_result_payload(), "summary": padding})
    assert len(content.encode("utf-8")) == 1_048_576
    assert VerificationRunner._parse_result(content)["summary"] == "x" * 8_000
    with pytest.raises(InvalidVerificationResultError, match="JSON input limit"):
        VerificationRunner._parse_result(content + " ")


def test_model_step_redaction_omits_malformed_json_but_preserves_plain_prose() -> None:
    assert redact_record_text('{"api_key":"private-marker",') == (
        "[malformed structured content omitted]"
    )
    assert redact_record_text('[{"token":"private-marker"}') == (
        "[malformed structured content omitted]"
    )
    assert redact_record_text("Ordinary response: Bearer synthetic-token") == (
        "Ordinary response: Bearer ***"
    )


@pytest.mark.parametrize(
    "content",
    [
        _invalid_result_examples()[0],
        _invalid_result_examples()[5],
        _invalid_result_examples()[6],
        _invalid_result_examples()[7],
        _invalid_result_examples()[8],
        json.dumps(
            {**_result_payload(), "summary": "\ud800"}, ensure_ascii=False
        ),
        _invalid_result_examples()[12],
        json.dumps(
            {
                **_result_payload(),
                "summary": "Bearer synthetic-token",
                "api_key": "private-marker",
            }
        ),
        '{"api_key":"private-marker",',
        '{"api_key":"private-marker","nested":'
        + "[" * 1_500
        + "0"
        + "]" * 1_500
        + "}",
    ],
    ids=[
        "duplicate-outcome",
        "over-depth",
        "over-byte-limit",
        "multibyte-over-byte-limit",
        "escaped-unpaired-surrogate",
        "literal-unpaired-surrogate",
        "truncated-json",
        "secret-bearing-unsupported-field",
        "secret-bearing-malformed-json",
        "deep-secret-bearing-json",
    ],
)
def test_invalid_json_closes_execution_without_echoing_unsafe_output(
    tmp_path, content: str
) -> None:
    repository, research, runner = _services(tmp_path, content)
    context = RequestContext(
        request_id="request-invalid-json",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    claim = _claim(research, context)
    plan = runner.create_plan(context, claim["id"], _draft())

    with pytest.raises(InvalidVerificationResultError):
        asyncio.run(runner.execute(context, plan["id"]))

    detail = research.get_claim(context, claim["id"])
    execution = detail["verification_executions"][0]
    attempt = detail["verification_attempts"][-1]
    gate = detail["promotion_evaluations"][0]
    run = repository.get_run(context.workspace_id, execution["run_id"])
    artifact = repository.get_artifact(context.workspace_id, execution["artifact_id"])
    assert execution["status"] == "invalid_output"
    assert execution["error_code"] == "invalid_verification_result"
    assert attempt["outcome"] == "error"
    assert gate["decision"] == "blocked"
    assert run["status"] == "failed"
    assert run["error_code"] == "invalid_verification_result"
    assert artifact is not None
    assert json.loads(artifact["content_text"])["raw_output_excerpt"] == (
        "[invalid or unsafe verification JSON omitted]"
    )
    assert "private-marker" not in artifact["content_text"]
    assert "synthetic-token" not in artifact["content_text"]
    assert all(
        "private-marker" not in step["output_content"]
        and "synthetic-token" not in step["output_content"]
        for step in run["steps"]
    )


def test_verification_runner_versions_plan_and_closes_evidence_chain(tmp_path) -> None:
    response = json.dumps(
        {
            "contract_version": RESULT_CONTRACT_VERSION,
            "outcome": "passed",
            "summary": "The declared source reference is structurally present.",
            "findings": ["The source reference has a locator and SHA-256 digest."],
            "evidence_refs": ["SRC-FIXTURE"],
            "limitations": ["The referenced external content was not fetched."],
        }
    )
    repository, research, runner = _services(tmp_path, response)
    context = RequestContext(
        request_id="request-1",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    claim = _claim(research, context)
    first = runner.create_plan(context, claim["id"], _draft())
    second = runner.create_plan(context, claim["id"], _draft())

    assert first["version"] == 1
    assert second["version"] == 2
    assert runner.get_plan(context, first["id"])["status"] == "retired"
    with pytest.raises(InvalidEvidenceError, match="active verification plan"):
        asyncio.run(runner.execute(context, first["id"]))

    result = asyncio.run(runner.execute(context, second["id"]))

    assert result["execution"]["status"] == "succeeded"
    assert result["execution"]["outcome"] == "passed"
    assert result["execution"]["run_id"] == result["run"]["id"]
    assert result["execution"]["artifact_id"] == result["artifact"]["id"]
    assert result["execution"]["attempt_id"] == result["attempt"]["id"]
    assert result["execution"]["promotion_evaluation_id"] == result["promotion"][
        "evaluation"
    ]["id"]
    assert result["run"]["steps"][-1]["name"] == "verification_result_contract"
    assert result["attempt"]["receipt"]["run_id"] == result["run"]["id"]
    assert result["attempt"]["plan_id"] == second["id"]
    assert result["attempt"]["verification_execution_id"] == result["execution"]["id"]
    assert result["attempt"]["independent"] is False
    assert result["promotion"]["evaluation"]["decision"] == "passed"
    assert result["promotion"]["evaluation"]["input_snapshot"]["evaluation_mode"] == (
        "proposal_only"
    )
    assert result["promotion"]["transition_applied"] is False
    assert result["promotion"]["claim"]["promotion_stage"] == "registered"
    assert result["execution"]["metadata"]["promotion_transition_applied"] is False
    assert (
        repository.get_artifact("workspace-a", result["artifact"]["id"])["content_hash"]
        == result["execution"]["output_digest"]
    )

    detail = research.get_claim(context, claim["id"])
    assert [item["version"] for item in detail["verification_plans"]] == [2, 1]
    assert detail["verification_executions"][0]["status"] == "succeeded"

    archive, _manifest = AssuranceBundleService(lambda: repository).export_zip(
        context, claim["research_case_id"]
    )
    bundle_path = tmp_path / "verified-assurance.zip"
    bundle_path.write_bytes(archive)
    report = AssuranceBundleVerifier().verify(bundle_path)
    assert report["valid"] is True
    assert (
        next(
            item
            for item in report["checks"]
            if item["code"] == f"attempt_input_digest:{result['attempt']['id']}"
        )["status"]
        == "passed"
    )
    assert (
        next(
            item
            for item in report["checks"]
            if item["code"] == f"promotion_digest:{result['promotion']['evaluation']['id']}"
        )["status"]
        == "passed"
    )


def test_invalid_agent_result_is_retained_as_error_receipt_and_blocked_gate(
    tmp_path,
) -> None:
    repository, research, runner = _services(tmp_path, "not-json")
    context = RequestContext(
        request_id="request-1",
        workspace_id="workspace-a",
        principal_id="admin-a",
    )
    claim = _claim(research, context)
    plan = runner.create_plan(context, claim["id"], _draft())
    prior_artifact = ArtifactService(
        repository_provider=lambda: repository
    ).create_artifact(
        context,
        ArtifactDraft(
            name="prior-result.json",
            kind=ArtifactKind.JSON,
            media_type="application/json",
            content_text='{"passed":true}',
        ),
    )
    research.record_verification_attempt(
        context,
        claim["id"],
        VerificationAttemptDraft(
            kind=VerificationKind.SOURCE_AUDIT,
            outcome=VerificationOutcome.PASSED,
            method="A previous local source audit.",
            scope="Fixture only.",
            input_digest="1" * 64,
            output_digest="2" * 64,
            artifact_ids=(prior_artifact["id"],),
        ),
    )

    with pytest.raises(InvalidVerificationResultError):
        asyncio.run(runner.execute(context, plan["id"]))

    detail = research.get_claim(context, claim["id"])
    execution = detail["verification_executions"][0]
    attempt = detail["verification_attempts"][-1]
    gate = detail["promotion_evaluations"][0]
    run = repository.get_run("workspace-a", execution["run_id"])

    assert execution["status"] == "invalid_output"
    assert execution["error_code"] == "invalid_verification_result"
    assert attempt["outcome"] == "error"
    assert attempt["receipt_id"]
    assert gate["decision"] == "blocked"
    assert gate["blockers"] == ["triggering_attempt_passed"]
    assert run["status"] == "failed"
    assert run["error_code"] == "invalid_verification_result"
    assert run["artifacts"][0]["id"] == execution["artifact_id"]


def test_verification_target_is_allowlisted_and_validates_payload(tmp_path) -> None:
    content = json.dumps(
        {
            "contract_version": RESULT_CONTRACT_VERSION,
            "outcome": "inconclusive",
            "summary": "No conclusion.",
            "findings": [],
            "evidence_refs": [],
            "limitations": ["Fixture."],
        }
    )
    repository, _research, verification = _services(tmp_path, content)
    task_runner = TaskRunner(
        gateway_service=FakeGateway(repository, content),
        verification_runner=verification,
    )

    assert "research.verify" in task_runner.target_names
    task_runner.validate("research.verify", {"plan_id": "plan-1"})
    with pytest.raises(InvalidScheduleError):
        task_runner.validate(
            "research.verify", {"plan_id": "plan-1", "scopes": ["tools:write"]}
        )
