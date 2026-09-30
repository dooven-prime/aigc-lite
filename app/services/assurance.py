"""Deterministic export and offline verification of portable assurance bundles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.assurance import ASSURANCE_BUNDLE_VERSION, VerificationIndependence
from ..core.contracts import RequestContext
from ..core.errors import InvalidAssuranceBundleError, ResourceNotFoundError
from ..database import get_repository
from ..repository import Repository
from .qualification import QualificationService

RepositoryProvider = Callable[[], Repository]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_DOCUMENTS = {
    "artifact.json",
    "claims.json",
    "provenance.json",
    "receipts.json",
    "evidence.json",
    "verification.json",
    "qualification.json",
    "reviews.json",
    "limitations.json",
    "signatures/status.json",
}
_MAX_MEMBERS = 10_000
_MAX_MEMBER_BYTES = 20 * 1024 * 1024
_MAX_TOTAL_BYTES = 100 * 1024 * 1024


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def json_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


class AssuranceBundleService:
    """Project one research case into a deterministic, read-only ZIP snapshot."""

    def __init__(self, repository_provider: RepositoryProvider = get_repository) -> None:
        self._repository_provider = repository_provider
        self._qualification = QualificationService(repository_provider)

    def export_zip(
        self, context: RequestContext, research_case_id: str
    ) -> tuple[bytes, dict[str, Any]]:
        files, manifest = self._build_files(context, research_case_id)
        stream = io.BytesIO()
        with zipfile.ZipFile(
            stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(files):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, files[path], compresslevel=9)
        return stream.getvalue(), manifest

    def _build_files(
        self, context: RequestContext, research_case_id: str
    ) -> tuple[dict[str, bytes], dict[str, Any]]:
        repository = self._repository_provider()
        research_case = repository.get_research_case(context.workspace_id, research_case_id)
        if research_case is None:
            raise ResourceNotFoundError("research_case", research_case_id)
        protocol = repository.get_evidence_protocol(
            context.workspace_id, research_case["protocol_id"]
        )
        if protocol is None:
            raise ResourceNotFoundError("evidence_protocol", research_case["protocol_id"])
        claims = repository.list_research_claims(
            context.workspace_id,
            research_case_id=research_case_id,
            limit=2_000,
        )
        relations = repository.list_research_claim_relations(
            context.workspace_id, research_case_id=research_case_id
        )

        attempts: list[dict] = []
        plans: list[dict] = []
        executions: list[dict] = []
        promotions: list[dict] = []
        qualification_evaluations: list[dict] = []
        qualification_receipts_by_id: dict[str, dict] = {}
        knowledge_admissions_by_id: dict[str, dict] = {}
        current_use_bindings: list[dict] = []
        for claim in claims:
            claim_id = claim["id"]
            attempts.extend(
                repository.list_research_verification_attempts(context.workspace_id, claim_id)
            )
            plans.extend(
                repository.list_research_verification_plans(context.workspace_id, claim_id)
            )
            executions.extend(
                repository.list_research_verification_executions(context.workspace_id, claim_id)
            )
            promotions.extend(
                repository.list_research_promotion_evaluations(context.workspace_id, claim_id)
            )
            claim_evaluations = repository.list_qualification_evaluations(
                context.workspace_id, claim_id
            )
            qualification_evaluations.extend(claim_evaluations)
            for receipt in repository.list_qualification_receipts(
                context.workspace_id, claim_id
            ):
                qualification_receipts_by_id[receipt["id"]] = receipt
            for admission in repository.list_knowledge_admission_receipts(
                context.workspace_id, claim_id
            ):
                knowledge_admissions_by_id[admission["id"]] = admission
            for profile_id in sorted({item["profile_id"] for item in claim_evaluations}):
                binding = repository.get_current_use_binding(
                    context.workspace_id, claim_id, profile_id
                )
                if binding is None:
                    continue
                refreshed = self._qualification.refresh_receipt_binding(
                    context, binding["qualification_receipt_id"]
                )
                current_use_bindings.append(refreshed or binding)

        authorization_grants = repository.list_authorization_grants(
            context.workspace_id, sorted(qualification_receipts_by_id)
        )

        receipts = list(protocol.get("receipts", []))
        reviews = [review for receipt in receipts for review in receipt.pop("reviews", [])]
        artifact_ids = {
            research_case.get("source_artifact_id"),
            research_case.get("source_ledger_artifact_id"),
            *(artifact_id for receipt in receipts for artifact_id in receipt["artifact_ids"]),
            *(artifact_id for attempt in attempts for artifact_id in attempt["artifact_ids"]),
            *(execution.get("artifact_id") for execution in executions),
        }
        artifact_ids.discard(None)
        artifacts = [
            artifact
            for artifact_id in sorted(artifact_ids)
            if (artifact := repository.get_artifact(context.workspace_id, str(artifact_id)))
            is not None
        ]

        run_ids = {
            *(receipt.get("run_id") for receipt in receipts),
            *(attempt.get("run_id") for attempt in attempts),
            *(execution.get("run_id") for execution in executions),
        }
        run_ids.discard(None)
        runs = [
            run
            for run_id in sorted(run_ids)
            if (run := repository.get_run(context.workspace_id, str(run_id))) is not None
        ]
        citations_by_id: dict[str, dict] = {}
        for artifact in artifacts:
            for citation in repository.list_citations(
                context.workspace_id, 500, artifact_id=artifact["id"]
            ):
                citations_by_id[citation["id"]] = citation
        for run_id in run_ids:
            for citation in repository.list_citations(
                context.workspace_id, 500, run_id=str(run_id)
            ):
                citations_by_id[citation["id"]] = citation

        sources_by_id = {
            source["id"]: source for claim in claims for source in claim.get("sources", [])
        }
        files: dict[str, bytes] = {}
        artifact_records = []
        for artifact in artifacts:
            payload = (artifact.get("content_text") or artifact.get("uri") or "").encode("utf-8")
            suffix = ".json" if artifact.get("media_type") == "application/json" else ".txt"
            safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", artifact["id"])
            payload_path = f"payloads/{safe_id}{suffix}"
            files[payload_path] = payload
            record = dict(artifact)
            record.pop("content_text", None)
            record["payload_path"] = payload_path
            artifact_records.append(record)

        protocol_record = {
            key: value
            for key, value in protocol.items()
            if key not in {"claims", "receipts", "freezes", "artifacts"}
        }
        limitations = self._limitations(
            research_case,
            list(sources_by_id.values()),
            list(citations_by_id.values()),
            attempts,
            executions,
        )
        documents: dict[str, object] = {
            "artifact.json": {
                "contract_version": "assurance.artifacts.v1",
                "artifacts": artifact_records,
            },
            "claims.json": {
                "contract_version": "assurance.claims.v1",
                "claims": claims,
                "relations": relations,
            },
            "provenance.json": {
                "contract_version": "assurance.provenance.v1",
                "research_case": research_case,
                "protocol": protocol_record,
                "sources": sorted(sources_by_id.values(), key=lambda item: item["id"]),
                "runs": runs,
            },
            "receipts.json": {
                "contract_version": "assurance.receipts.v1",
                "receipts": receipts,
            },
            "evidence.json": {
                "contract_version": "assurance.evidence.v1",
                "claims": protocol.get("claims", []),
                "citations": sorted(citations_by_id.values(), key=lambda item: item["id"]),
                "freezes": protocol.get("freezes", []),
            },
            "verification.json": {
                "contract_version": "assurance.verification.v1",
                "plans": plans,
                "executions": executions,
                "attempts": attempts,
                "promotion_evaluations": promotions,
            },
            "qualification.json": {
                "contract_version": "qualification.bundle-projection.v1",
                "knowledge_admission_policies": (
                    self._qualification.list_knowledge_admission_policies()
                ),
                "evaluations": qualification_evaluations,
                "receipts": sorted(
                    qualification_receipts_by_id.values(),
                    key=lambda item: item["id"],
                ),
                "knowledge_admissions": sorted(
                    knowledge_admissions_by_id.values(),
                    key=lambda item: item["id"],
                ),
                "current_use_bindings": sorted(current_use_bindings, key=lambda item: item["id"]),
                "authorization_grants": authorization_grants,
            },
            "reviews.json": {
                "contract_version": "assurance.reviews.v1",
                "reviews": reviews,
            },
            "limitations.json": {
                "contract_version": "assurance.limitations.v1",
                "limitations": limitations,
            },
            "signatures/status.json": {
                "contract_version": "assurance.signatures.v1",
                "status": "unsigned",
                "signatures": [],
                "limitation": (
                    "Member hashes establish integrity after export, not publisher identity."
                ),
            },
        }
        for path, value in documents.items():
            files[path] = canonical_json(value)

        members = [
            {
                "path": path,
                "sha256": hashlib.sha256(files[path]).hexdigest(),
                "size_bytes": len(files[path]),
                "media_type": ("application/json" if path.endswith(".json") else "text/plain"),
                "role": self._role(path),
            }
            for path in sorted(files)
        ]
        assurance_snapshot = _assurance_vector(documents, integrity_ok=True)
        manifest = {
            "contract_version": ASSURANCE_BUNDLE_VERSION,
            "research_case_id": research_case_id,
            "profile": research_case["profile"],
            "registry_id": research_case["registry_id"],
            "registry_version": research_case["registry_version"],
            "snapshot_created_at": research_case["created_at"],
            "bundle_digest": json_digest(members),
            "members": members,
            "assurance_snapshot": assurance_snapshot,
        }
        files["manifest.json"] = canonical_json(manifest)
        return files, manifest

    @staticmethod
    def _role(path: str) -> str:
        if path.startswith("payloads/"):
            return "artifact_payload"
        if path.startswith("signatures/"):
            return "signature_status"
        return path.removesuffix(".json")

    @staticmethod
    def _limitations(
        research_case: dict,
        sources: list[dict],
        citations: list[dict],
        attempts: list[dict],
        executions: list[dict],
    ) -> list[dict[str, str]]:
        values = [
            {
                "code": "bundle_unsigned",
                "scope": "identity",
                "statement": "The bundle has hash closure but no publisher signature.",
            },
            {
                "code": "domain_semantics_not_re_evaluated",
                "scope": "verification",
                "statement": (
                    "The portable verifier checks declared process closure; domain truth "
                    "still requires a profile-specific validator."
                ),
            },
        ]
        if research_case.get("metadata", {}).get("structural_validation_only"):
            values.append(
                {
                    "code": "structural_source_validation_only",
                    "scope": "evidence_closure",
                    "statement": (
                        "Declared source locators and digests were checked structurally; "
                        "external source bytes were not independently fetched."
                    ),
                }
            )
        if any(source.get("status") == "declared" for source in sources):
            values.append(
                {
                    "code": "declared_sources_not_embedded",
                    "scope": "evidence_closure",
                    "statement": "One or more research sources are declarations, not embedded witnesses.",
                }
            )
        if any(citation.get("source_kind") in {"url", "document"} for citation in citations):
            values.append(
                {
                    "code": "external_citation_content_not_embedded",
                    "scope": "provenance",
                    "statement": "External citation targets may require separate retrieval.",
                }
            )
        if not attempts:
            values.append(
                {
                    "code": "no_verification_attempts",
                    "scope": "verification",
                    "statement": "No verification attempt is present in this snapshot.",
                }
            )
        if any(attempt.get("independence", {}).get("legacy_declaration") for attempt in attempts):
            values.append(
                {
                    "code": "legacy_independence_declaration",
                    "scope": "independence",
                    "statement": "A legacy boolean declaration lacks structured overlap evidence.",
                }
            )
        if any(execution.get("status") == "running" for execution in executions):
            values.append(
                {
                    "code": "verification_execution_in_progress",
                    "scope": "reproducibility",
                    "statement": "The snapshot contains a non-terminal verification execution.",
                }
            )
        return values


class AssuranceBundleVerifier:
    """Verify a bundle without a database, network, model, or aigc-lite server."""

    def verify(self, source: str | Path) -> dict[str, Any]:
        files = self._read_files(Path(source))
        manifest = self._json(files, "manifest.json")
        if manifest.get("contract_version") != ASSURANCE_BUNDLE_VERSION:
            raise InvalidAssuranceBundleError(
                "Unsupported assurance bundle contract", field="contract_version"
            )
        members = manifest.get("members")
        if not isinstance(members, list) or len(members) > _MAX_MEMBERS:
            raise InvalidAssuranceBundleError("Manifest members are invalid", field="members")
        declared_paths: set[str] = set()
        checks: list[dict[str, Any]] = []
        normalized_members = []
        for member in members:
            if not isinstance(member, dict):
                raise InvalidAssuranceBundleError("Manifest member must be an object")
            path = member.get("path")
            self._safe_path(path)
            if path in declared_paths:
                raise InvalidAssuranceBundleError("Manifest contains a duplicate path")
            declared_paths.add(path)
            payload = files.get(path)
            expected_hash = member.get("sha256")
            expected_size = member.get("size_bytes")
            passed = (
                payload is not None
                and _SHA256.fullmatch(str(expected_hash or "")) is not None
                and hashlib.sha256(payload).hexdigest() == expected_hash
                and len(payload) == expected_size
            )
            checks.append({"code": f"member:{path}", "status": "passed" if passed else "failed"})
            normalized_members.append(member)
        extras = set(files) - declared_paths - {"manifest.json"}
        missing_documents = _REQUIRED_DOCUMENTS - declared_paths
        closure_ok = not extras and not missing_documents
        checks.append(
            {
                "code": "manifest_closure",
                "status": "passed" if closure_ok else "failed",
                "details": {
                    "extra_paths": sorted(extras),
                    "missing_documents": sorted(missing_documents),
                },
            }
        )
        digest_ok = json_digest(normalized_members) == manifest.get("bundle_digest")
        checks.append({"code": "bundle_digest", "status": "passed" if digest_ok else "failed"})

        documents = {path: self._json(files, path) for path in _REQUIRED_DOCUMENTS}
        self._verify_artifacts(files, documents, checks)
        self._verify_references(documents, checks)
        self._verify_replay_digests(documents, checks)
        self._verify_qualification(documents, checks)
        valid = all(check["status"] != "failed" for check in checks)
        return {
            "contract_version": "assurance.verification-report.v1",
            "valid": valid,
            "bundle_digest": manifest.get("bundle_digest"),
            "research_case_id": manifest.get("research_case_id"),
            "assurance": _assurance_vector(documents, integrity_ok=valid),
            "checks": checks,
            "limitations": documents["limitations.json"].get("limitations", []),
        }

    @staticmethod
    def _read_files(source: Path) -> dict[str, bytes]:
        if source.is_dir():
            files: dict[str, bytes] = {}
            total = 0
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise InvalidAssuranceBundleError("Bundle directories may not contain symlinks")
                if not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                AssuranceBundleVerifier._safe_path(relative)
                payload = path.read_bytes()
                if len(payload) > _MAX_MEMBER_BYTES:
                    raise InvalidAssuranceBundleError("Bundle member exceeds the size limit")
                total += len(payload)
                files[relative] = payload
            if total > _MAX_TOTAL_BYTES or len(files) > _MAX_MEMBERS + 1:
                raise InvalidAssuranceBundleError("Bundle exceeds offline verification limits")
            return files
        if not source.is_file() or not zipfile.is_zipfile(source):
            raise InvalidAssuranceBundleError("Bundle must be a directory or ZIP file")
        files = {}
        total = 0
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_MEMBERS + 1:
                raise InvalidAssuranceBundleError("Bundle contains too many members")
            for info in infos:
                if info.is_dir():
                    continue
                AssuranceBundleVerifier._safe_path(info.filename)
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise InvalidAssuranceBundleError("Bundle member exceeds the size limit")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise InvalidAssuranceBundleError("Bundle ZIP may not contain symlinks")
                total += info.file_size
                if total > _MAX_TOTAL_BYTES:
                    raise InvalidAssuranceBundleError("Bundle exceeds the size limit")
                if info.filename in files:
                    raise InvalidAssuranceBundleError("Bundle ZIP contains duplicate paths")
                files[info.filename] = archive.read(info)
        return files

    @staticmethod
    def _safe_path(path: object) -> None:
        if not isinstance(path, str) or not path or "\\" in path:
            raise InvalidAssuranceBundleError("Bundle contains an unsafe member path")
        value = PurePosixPath(path)
        if value.is_absolute() or ".." in value.parts or value.as_posix() != path:
            raise InvalidAssuranceBundleError("Bundle contains an unsafe member path")

    @staticmethod
    def _json(files: dict[str, bytes], path: str) -> dict:
        payload = files.get(path)
        if payload is None:
            raise InvalidAssuranceBundleError(f"Bundle is missing {path}")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidAssuranceBundleError(f"{path} is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise InvalidAssuranceBundleError(f"{path} must contain a JSON object")
        return value

    @staticmethod
    def _verify_artifacts(
        files: dict[str, bytes], documents: dict[str, dict], checks: list[dict]
    ) -> None:
        for artifact in documents["artifact.json"].get("artifacts", []):
            path = artifact.get("payload_path")
            payload = files.get(path)
            passed = (
                isinstance(path, str)
                and payload is not None
                and hashlib.sha256(payload).hexdigest() == artifact.get("content_hash")
            )
            checks.append(
                {
                    "code": f"artifact_payload:{artifact.get('id', 'unknown')}",
                    "status": "passed" if passed else "failed",
                }
            )

    @staticmethod
    def _verify_references(documents: dict[str, dict], checks: list[dict]) -> None:
        claims = documents["claims.json"].get("claims", [])
        relations = documents["claims.json"].get("relations", [])
        artifacts = documents["artifact.json"].get("artifacts", [])
        receipts = documents["receipts.json"].get("receipts", [])
        verification = documents["verification.json"]
        provenance = documents["provenance.json"]
        ids = {
            "claim": {item.get("id") for item in claims},
            "artifact": {item.get("id") for item in artifacts},
            "receipt": {item.get("id") for item in receipts},
            "plan": {item.get("id") for item in verification.get("plans", [])},
            "execution": {item.get("id") for item in verification.get("executions", [])},
            "run": {item.get("id") for item in provenance.get("runs", [])},
        }
        broken = []
        for relation in relations:
            for field in ("source_claim_id", "target_claim_id"):
                if relation.get(field) not in ids["claim"]:
                    broken.append(f"relation:{relation.get('id')}:{field}")
        for attempt in verification.get("attempts", []):
            if attempt.get("claim_revision_id") not in ids["claim"]:
                broken.append(f"attempt:{attempt.get('id')}:claim")
            if attempt.get("receipt_id") not in ids["receipt"]:
                broken.append(f"attempt:{attempt.get('id')}:receipt")
            for artifact_id in attempt.get("artifact_ids", []):
                if artifact_id not in ids["artifact"]:
                    broken.append(f"attempt:{attempt.get('id')}:artifact:{artifact_id}")
            if attempt.get("plan_id") and attempt["plan_id"] not in ids["plan"]:
                broken.append(f"attempt:{attempt.get('id')}:plan")
            if attempt.get("run_id") and attempt["run_id"] not in ids["run"]:
                broken.append(f"attempt:{attempt.get('id')}:run")
        checks.append(
            {
                "code": "reference_closure",
                "status": "passed" if not broken else "failed",
                "details": {"broken": broken},
            }
        )

    @staticmethod
    def _verify_replay_digests(documents: dict[str, dict], checks: list[dict]) -> None:
        artifacts = {item["id"]: item for item in documents["artifact.json"].get("artifacts", [])}
        verification = documents["verification.json"]
        plans = {item["id"]: item for item in verification.get("plans", [])}
        executions = {item["id"]: item for item in verification.get("executions", [])}
        for plan in plans.values():
            snapshot = {
                key: plan.get(key)
                for key in (
                    "plan_key",
                    "executor",
                    "name",
                    "kind",
                    "method",
                    "scope",
                    "prompt",
                    "system_prompt",
                    "model",
                    "result_contract_version",
                    "auto_promote",
                    "metadata",
                )
            }
            checks.append(
                {
                    "code": f"plan_digest:{plan['id']}",
                    "status": (
                        "passed"
                        if json_digest(snapshot) == plan.get("content_digest")
                        else "failed"
                    ),
                }
            )
        for attempt in verification.get("attempts", []):
            plan = plans.get(attempt.get("plan_id"))
            execution = executions.get(attempt.get("verification_execution_id"))
            input_snapshot = execution.get("input_snapshot") if execution else None
            if plan is None or not input_snapshot:
                checks.append(
                    {
                        "code": f"attempt_input_digest:{attempt.get('id')}",
                        "status": "undetermined",
                    }
                )
            else:
                snapshot_matches_entities = input_snapshot.get("plan", {}).get("id") == plan.get(
                    "id"
                ) and input_snapshot.get("claim", {}).get("id") == attempt.get("claim_revision_id")
                checks.append(
                    {
                        "code": f"attempt_input_digest:{attempt['id']}",
                        "status": (
                            "passed"
                            if snapshot_matches_entities
                            and json_digest(input_snapshot) == attempt.get("input_digest")
                            else "failed"
                        ),
                    }
                )
            output_matches = any(
                artifacts.get(artifact_id, {}).get("content_hash") == attempt.get("output_digest")
                for artifact_id in attempt.get("artifact_ids", [])
            )
            checks.append(
                {
                    "code": f"attempt_output_digest:{attempt.get('id')}",
                    "status": "passed" if output_matches else "undetermined",
                }
            )
            independence = attempt.get("independence") or {}
            if independence.get("derived"):
                qualified = independence.get("qualified") is True
                reasons = tuple(independence.get("limitations") or [])
            else:
                assessment = VerificationIndependence.from_dict(independence)
                qualified, reasons = assessment.qualification()
            legacy = bool(independence.get("legacy_declaration"))
            consistent = bool(attempt.get("independent")) == qualified
            checks.append(
                {
                    "code": f"independence:{attempt.get('id')}",
                    "status": ("undetermined" if legacy else "passed" if consistent else "failed"),
                    "details": {"qualified": qualified, "blockers": list(reasons)},
                }
            )
        for gate in verification.get("promotion_evaluations", []):
            snapshot = gate.get("input_snapshot")
            if not snapshot:
                checks.append(
                    {
                        "code": f"promotion_digest:{gate.get('id')}",
                        "status": "undetermined",
                    }
                )
                continue
            input_ok = json_digest(snapshot) == gate.get("input_digest")
            evaluation_ok = json_digest(
                {
                    "input_digest": gate.get("input_digest"),
                    "from_stage": gate.get("from_stage"),
                    "target_stage": gate.get("target_stage"),
                    "decision": gate.get("decision"),
                    "blockers": gate.get("blockers", []),
                }
            ) == gate.get("evaluation_digest")
            checks.append(
                {
                    "code": f"promotion_digest:{gate.get('id')}",
                    "status": "passed" if input_ok and evaluation_ok else "failed",
                }
            )

    @staticmethod
    def _verify_qualification(documents: dict[str, dict], checks: list[dict]) -> None:
        qualification = documents["qualification.json"]
        claim_ids = {item.get("id") for item in documents["claims.json"].get("claims", [])}
        evaluations = {item.get("id"): item for item in qualification.get("evaluations", [])}
        receipts = {item.get("id"): item for item in qualification.get("receipts", [])}
        admissions = {
            item.get("id"): item
            for item in qualification.get("knowledge_admissions", [])
        }
        admission_policies = {
            (item.get("policy_id"), item.get("version")): item
            for item in qualification.get("knowledge_admission_policies", [])
        }
        broken: list[str] = []
        for evaluation in evaluations.values():
            if evaluation.get("claim_revision_id") not in claim_ids:
                broken.append(f"evaluation:{evaluation.get('id')}:claim")
            if json_digest(evaluation.get("evidence_closure")) != evaluation.get(
                "evidence_closure_hash"
            ):
                broken.append(f"evaluation:{evaluation.get('id')}:closure_hash")
        for receipt in receipts.values():
            if receipt.get("evaluation_id") not in evaluations:
                broken.append(f"receipt:{receipt.get('id')}:evaluation")
            portable = {
                "qualification_receipt_id": receipt.get("id"),
                "claim_revision_id": receipt.get("claim_revision_id"),
                "claim_semantic_hash": receipt.get("claim_semantic_hash"),
                "profile": receipt.get("profile_id"),
                "profile_version": receipt.get("profile_version"),
                "evidence_closure_hash": receipt.get("evidence_closure_hash"),
                "policy_version": receipt.get("policy_version"),
                "policy_hash": receipt.get("policy_hash"),
                "verdict": receipt.get("verdict"),
                "criteria": receipt.get("criteria"),
                "blockers": receipt.get("blockers"),
                "evidence_vector": receipt.get("evidence_vector"),
                "independence_summary": receipt.get("independence_summary"),
                "issued_at": receipt.get("issued_at"),
            }
            if json_digest(portable) != receipt.get("receipt_hash"):
                broken.append(f"receipt:{receipt.get('id')}:receipt_hash")
        for admission in admissions.values():
            receipt = receipts.get(admission.get("qualification_receipt_id"))
            if receipt is None:
                broken.append(f"admission:{admission.get('id')}:qualification_receipt")
            portable = {
                "contract_version": admission.get("contract_version"),
                "knowledge_admission_receipt_id": admission.get("id"),
                "qualification_receipt_id": admission.get("qualification_receipt_id"),
                "qualification_receipt_hash": admission.get(
                    "qualification_receipt_hash"
                ),
                "claim_revision_id": admission.get("claim_revision_id"),
                "claim_semantic_hash": admission.get("claim_semantic_hash"),
                "profile_id": admission.get("profile_id"),
                "profile_version": admission.get("profile_version"),
                "use_scope": admission.get("use_scope"),
                "admission_policy_id": admission.get("admission_policy_id"),
                "admission_policy_version": admission.get(
                    "admission_policy_version"
                ),
                "admission_policy_hash": admission.get("admission_policy_hash"),
                "approved_by": admission.get("approved_by"),
                "rationale": admission.get("rationale"),
                "issued_at": admission.get("issued_at"),
            }
            if json_digest(portable) != admission.get("receipt_hash"):
                broken.append(f"admission:{admission.get('id')}:receipt_hash")
            if receipt is not None and (
                admission.get("qualification_receipt_hash")
                != receipt.get("receipt_hash")
            ):
                broken.append(f"admission:{admission.get('id')}:receipt_binding")
            policy = admission_policies.get(
                (
                    admission.get("admission_policy_id"),
                    admission.get("admission_policy_version"),
                )
            )
            if policy is None:
                broken.append(f"admission:{admission.get('id')}:policy")
            elif json_digest(policy) != admission.get("admission_policy_hash"):
                broken.append(f"admission:{admission.get('id')}:policy_hash")
        for binding in qualification.get("current_use_bindings", []):
            if binding.get("qualification_receipt_id") not in receipts:
                broken.append(f"binding:{binding.get('id')}:receipt")
            admission = admissions.get(binding.get("knowledge_admission_receipt_id"))
            if binding.get("use_scope") == "knowledge" and binding.get("state") == "current":
                if admission is None:
                    broken.append(f"binding:{binding.get('id')}:knowledge_admission")
                elif (
                    admission.get("qualification_receipt_id")
                    != binding.get("qualification_receipt_id")
                ):
                    broken.append(f"binding:{binding.get('id')}:admission_mismatch")
        for grant in qualification.get("authorization_grants", []):
            if grant.get("qualification_receipt_id") not in receipts:
                broken.append(f"grant:{grant.get('id')}:receipt")
            receipt = receipts.get(grant.get("qualification_receipt_id"), {})
            grant_payload = {
                "qualification_receipt_id": grant.get("qualification_receipt_id"),
                "qualification_receipt_hash": receipt.get("receipt_hash"),
                "actor_id": grant.get("actor_id"),
                "action": grant.get("action"),
                "target": grant.get("target"),
                "scope": grant.get("scope"),
                "conditions": grant.get("conditions"),
                "expires_at": grant.get("expires_at"),
                "budget": grant.get("budget"),
                "max_calls": grant.get("max_calls"),
                "policy_version": grant.get("policy_version"),
            }
            if json_digest(grant_payload) != grant.get("grant_receipt"):
                broken.append(f"grant:{grant.get('id')}:grant_receipt")
        checks.append(
            {
                "code": "qualification_closure",
                "status": "passed" if not broken else "failed",
                "details": {"broken": broken},
            }
        )


def _assurance_vector(documents: dict[str, object], *, integrity_ok: bool) -> dict[str, Any]:
    claims_doc = documents.get("claims.json", {})
    verification_doc = documents.get("verification.json", {})
    provenance_doc = documents.get("provenance.json", {})
    evidence_doc = documents.get("evidence.json", {})
    qualification_doc = documents.get("qualification.json", {})
    signature_doc = documents.get("signatures/status.json", {})
    claims = claims_doc.get("claims", []) if isinstance(claims_doc, dict) else []
    attempts = verification_doc.get("attempts", []) if isinstance(verification_doc, dict) else []
    sources = provenance_doc.get("sources", []) if isinstance(provenance_doc, dict) else []
    citations = evidence_doc.get("citations", []) if isinstance(evidence_doc, dict) else []
    qualified_attempts = []
    for attempt in attempts:
        independence = attempt.get("independence") or {}
        if independence.get("derived"):
            qualified = independence.get("qualified") is True
        else:
            assessment = VerificationIndependence.from_dict(independence)
            qualified, _reasons = assessment.qualification()
        if qualified and attempt.get("outcome") == "passed":
            qualified_attempts.append(attempt["id"])
    qualification_receipts = (
        qualification_doc.get("receipts", []) if isinstance(qualification_doc, dict) else []
    )
    bindings = (
        qualification_doc.get("current_use_bindings", [])
        if isinstance(qualification_doc, dict)
        else []
    )
    admissions = (
        qualification_doc.get("knowledge_admissions", [])
        if isinstance(qualification_doc, dict)
        else []
    )
    admission_ids = {item.get("id") for item in admissions}
    current_receipt_ids = {
        item.get("qualification_receipt_id")
        for item in bindings
        if item.get("state") == "current"
        and item.get("knowledge_admission_receipt_id") in admission_ids
    }
    current_receipts = [
        item
        for item in qualification_receipts
        if item.get("id") in current_receipt_ids and item.get("verdict") == "ADMITTED"
    ]
    grants = (
        qualification_doc.get("authorization_grants", [])
        if isinstance(qualification_doc, dict)
        else []
    )
    evaluated_at = datetime.now(UTC)
    active_grants = [
        item
        for item in grants
        if item.get("state") == "active"
        and item.get("qualification_receipt_id") in current_receipt_ids
        and int(item.get("calls_used") or 0) < int(item.get("max_calls") or 0)
        and _grant_not_expired(item, evaluated_at)
    ]
    passed_by_claim = {
        attempt.get("claim_revision_id")
        for attempt in attempts
        if attempt.get("outcome") == "passed"
    }
    failed_by_claim = {
        attempt.get("claim_revision_id")
        for attempt in attempts
        if attempt.get("outcome") in {"failed", "error"}
    }
    closed_claims = {claim.get("id") for claim in claims if claim.get("closure_status") == "closed"}
    all_claim_ids = {claim.get("id") for claim in claims}
    if failed_by_claim or any(claim.get("closure_status") == "blocked" for claim in claims):
        epistemic = "insufficient"
    elif current_receipts:
        epistemic = "supported"
    elif all_claim_ids and all_claim_ids <= passed_by_claim and all_claim_ids <= closed_claims:
        epistemic = "supported"
    else:
        epistemic = "undetermined"
    authority = "authorized" if integrity_ok and active_grants else "blocked"
    if any(attempt.get("outcome") in {"failed", "error"} for attempt in attempts):
        verification_status = "failed"
    elif attempts and all(attempt.get("outcome") == "passed" for attempt in attempts):
        verification_status = "passed"
    elif attempts:
        verification_status = "mixed"
    else:
        verification_status = "not_run"
    return {
        "identity": {
            "status": "verified" if integrity_ok else "failed",
            "publisher_authentication": signature_doc.get("status", "unknown"),
        },
        "provenance": {
            "status": "traceable" if sources or citations else "insufficient",
            "source_count": len(sources),
            "citation_count": len(citations),
        },
        "reproducibility": {
            "status": "replayable"
            if attempts and all(item.get("plan_id") for item in attempts)
            else "partial",
            "attempt_count": len(attempts),
        },
        "evidence_closure": {
            "status": (
                "declared_closed" if claims and all_claim_ids <= closed_claims else "blocked"
            ),
            "closed_claims": len(closed_claims),
            "total_claims": len(claims),
        },
        "verification": {
            "status": verification_status,
            "domain_semantics": "not_evaluated_by_bundle_verifier",
        },
        "orthogonal_verification": {
            "status": (
                "satisfied"
                if any(
                    "orthogonal_non_llm_checker"
                    in (item.get("independence") or {}).get(
                        "verification_properties", []
                    )
                    for item in attempts
                )
                else "undetermined"
            )
        },
        "independence": {
            "status": (
                "qualified"
                if qualified_attempts
                or any(
                    item.get("independence_summary", {}).get("qualified") is True
                    for item in current_receipts
                )
                else "not_qualified"
            ),
            "qualified_attempt_ids": qualified_attempts,
        },
        "epistemic_state": {
            "status": epistemic,
            "current_qualification_receipt_ids": sorted(current_receipt_ids),
        },
        "authority_state": {
            "status": authority,
            "authorization_grant_ids": [item.get("id") for item in active_grants],
        },
    }


def _grant_not_expired(grant: dict[str, Any], evaluated_at: datetime) -> bool:
    expires_at = grant.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry.tzinfo is not None and expiry > evaluated_at
