"""A deliberately small, deterministic A/B propositional qualification profile.

This is not a natural-language entailment engine. It checks exactly two frozen
premises over A and B and independently recomputes all four truth assignments.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.qualification import (
    CriterionReceipt,
    EvidenceAxisState,
    EvidenceClosure,
    QualificationCriterion,
    QualificationDecision,
    QualificationProfile,
    QualificationVerdict,
    ValidationModality,
    canonical_hash,
)

CLAIM_KIND = "propositional_consequence"
PREMISE_CONTRACT = "logic.premise.ab.v1"
CERTIFICATE_CONTRACT = "logic.entailment.ab.v1"
PREMISE_PRINCIPAL = "system:verifier:logic-premise"
ENTAILMENT_PRINCIPAL = "system:verifier:logic-entailment"
_FORMULAS = frozenset({"A", "B", "A=>B", "B=>A"})
_CONCLUSIONS = frozenset({"A", "B"})

PROFILE = QualificationProfile(
    profile_id="logic.propositional.ab.v1",
    version=1,
    claim_kind=CLAIM_KIND,
    policy_version="logic.propositional.ab.policy.v1",
    description=(
        "Two source-bound, separately checked premises over A/B; an exact "
        "server-derived truth-table certificate must entail the frozen conclusion. "
        "This establishes only finite propositional consequence."
    ),
    criteria=(
        QualificationCriterion("statement_identity", "Exact frozen ClaimRevision identity."),
        QualificationCriterion(
            "premise_source_closure", "Two intact, source-bound premise Artifacts."
        ),
        QualificationCriterion("premise_validation", "Each premise has a system-derived check."),
        QualificationCriterion(
            "entailment_certificate", "A bound truth table was independently recomputed."
        ),
        QualificationCriterion(
            "logical_entailment", "Every satisfying assignment of the premises satisfies the claim."
        ),
    ),
    accepted_modalities=(ValidationModality.EXACT_REPLAY, ValidationModality.LOGICAL_ENTAILMENT),
)


def _formula_value(formula: str, assignment: dict[str, bool]) -> bool:
    if formula in {"A", "B"}:
        return assignment[formula]
    if formula == "A=>B":
        return not assignment["A"] or assignment["B"]
    if formula == "B=>A":
        return not assignment["B"] or assignment["A"]
    raise ValueError("Formula is outside the frozen A/B grammar")


def certificate_input_digest(
    claim_revision_id: str, claim_semantic_hash: str, premises: list[dict[str, str]]
) -> str:
    return canonical_hash(
        {
            "claim_revision_id": claim_revision_id,
            "claim_semantic_hash": claim_semantic_hash,
            "premises": premises,
        }
    )


def build_entailment_certificate(
    claim_revision_id: str,
    claim_semantic_hash: str,
    premises: list[dict[str, str]],
    conclusion: str,
) -> dict[str, Any]:
    """Return the full finite check, including a countermodel when one exists."""
    if (
        conclusion not in _CONCLUSIONS
        or len(premises) != 2
        or [item.get("key") for item in premises] != ["E1", "E2"]
        or any(item.get("formula") not in _FORMULAS for item in premises)
    ):
        raise ValueError("Only two E1/E2 premises and an A/B conclusion are supported")
    rows = []
    countermodel = None
    for a in (False, True):
        for b in (False, True):
            assignment = {"A": a, "B": b}
            premise_values = [_formula_value(item["formula"], assignment) for item in premises]
            conclusion_value = _formula_value(conclusion, assignment)
            rows.append(
                {
                    "assignment": assignment,
                    "premises": premise_values,
                    "conclusion": conclusion_value,
                }
            )
            if all(premise_values) and not conclusion_value and countermodel is None:
                countermodel = assignment
    return {
        "contract_version": CERTIFICATE_CONTRACT,
        "claim_revision_id": claim_revision_id,
        "claim_semantic_hash": claim_semantic_hash,
        "premises": premises,
        "conclusion": conclusion,
        "truth_table": rows,
        "entailed": countermodel is None,
        "countermodel": countermodel,
    }


def _artifact_intact(artifact: dict[str, Any]) -> bool:
    payload = artifact["payload"]
    content = payload.get("content_text")
    return (
        isinstance(content, str)
        and hashlib.sha256(content.encode("utf-8")).hexdigest() == artifact["content_hash"]
        and artifact["content_hash"] == payload.get("content_hash")
    )


def _system_attempt(
    attempt: dict[str, Any], *, principal: str, modality: ValidationModality
) -> bool:
    payload = attempt["payload"]
    lineage = payload.get("verifier_lineage") or {}
    return (
        not payload.get("invalidated_by")
        and payload.get("validation_modality") == modality.value
        and lineage.get("runtime_derived") is True
        and lineage.get("principal_id") == principal
        and not lineage.get("model_route")
    )


def _criterion(
    code: str,
    state: EvidenceAxisState,
    reason: str,
    nodes: tuple[str, ...] = (),
) -> CriterionReceipt:
    return CriterionReceipt(code, state, reason, nodes)


class PropositionalABVerifier:
    profile = PROFILE

    def evaluate(self, closure: EvidenceClosure) -> QualificationDecision:
        claim = next(item for item in closure.nodes if item["node_type"] == "claim_revision")
        subject = claim["payload"]
        if subject.get("claim_type") != CLAIM_KIND:
            return QualificationDecision(
                QualificationVerdict.NOT_APPLICABLE, (), ("claim_kind_mismatch",)
            )
        identity_ok = (
            bool(subject.get("semantic_hash"))
            and subject["semantic_hash"] == closure.claim_semantic_hash
            and "statement_drift" not in closure.limitations
        )
        if not identity_ok:
            return QualificationDecision(
                QualificationVerdict.STALE,
                (
                    _criterion(
                        "statement_identity",
                        EvidenceAxisState.FAILED,
                        "The frozen ClaimRevision semantic hash no longer matches.",
                        (closure.claim_revision_id,),
                    ),
                ),
                ("statement_drift",),
            )
        conclusion = subject.get("statement")
        if conclusion not in _CONCLUSIONS:
            return QualificationDecision(
                QualificationVerdict.NOT_APPLICABLE, (), ("unsupported_conclusion",)
            )

        artifacts = {
            item["node_id"]: item for item in closure.nodes if item["node_type"] == "artifact"
        }
        sources = [item for item in closure.nodes if item["node_type"] == "source"]
        attempts = [item for item in closure.nodes if item["node_type"] == "verification_attempt"]
        premise_artifacts = [
            item
            for item in artifacts.values()
            if (item["payload"].get("metadata") or {}).get("role") == "logic_premise"
        ]
        premises: list[dict[str, str]] = []
        premise_attempt_ids: list[str] = []
        for artifact in premise_artifacts:
            if not _artifact_intact(artifact):
                continue
            try:
                value = json.loads(artifact["payload"]["content_text"])
            except (TypeError, ValueError):
                continue
            if (
                not isinstance(value, dict)
                or set(value) != {"contract_version", "key", "formula"}
                or value["contract_version"] != PREMISE_CONTRACT
                or not isinstance(value["key"], str)
                or value["key"] not in {"E1", "E2"}
                or not isinstance(value["formula"], str)
                or value["formula"] not in _FORMULAS
            ):
                continue
            premises.append(
                {
                    "key": value["key"],
                    "artifact_id": artifact["node_id"],
                    "sha256": artifact["content_hash"],
                    "formula": value["formula"],
                }
            )
        premises.sort(key=lambda item: item["key"])
        source_ok = (
            len(premise_artifacts) == 2
            and len(premises) == 2
            and len(sources) == 2
            and [item["key"] for item in premises] == ["E1", "E2"]
            and all(
                any(
                    source["payload"].get("source_key") == item["key"]
                    and source["payload"].get("locator") == f"artifact:{item['artifact_id']}"
                    and source["content_hash"] == item["sha256"]
                    and source["payload"].get("status") == "frozen"
                    for source in sources
                )
                for item in premises
            )
        )
        if source_ok:
            for premise in premises:
                checked = next(
                    (
                        attempt
                        for attempt in attempts
                        if _system_attempt(
                            attempt,
                            principal=PREMISE_PRINCIPAL,
                            modality=ValidationModality.EXACT_REPLAY,
                        )
                        and attempt["payload"].get("outcome") == "passed"
                        and attempt["payload"].get("artifact_ids") == [premise["artifact_id"]]
                        and attempt["payload"].get("input_digest") == premise["sha256"]
                        and attempt["payload"].get("output_digest") == premise["sha256"]
                    ),
                    None,
                )
                if checked is not None:
                    premise_attempt_ids.append(checked["node_id"])
        premise_checks_ok = source_ok and len(premise_attempt_ids) == 2

        expected = (
            build_entailment_certificate(
                closure.claim_revision_id,
                closure.claim_semantic_hash,
                premises,
                conclusion,
            )
            if premise_checks_ok
            else None
        )
        certificate_attempt_ids: list[str] = []
        certificate_ok = False
        if expected is not None:
            input_digest = certificate_input_digest(
                closure.claim_revision_id, closure.claim_semantic_hash, premises
            )
            for attempt in attempts:
                if (
                    not _system_attempt(
                        attempt,
                        principal=ENTAILMENT_PRINCIPAL,
                        modality=ValidationModality.LOGICAL_ENTAILMENT,
                    )
                    or attempt["payload"].get("input_digest") != input_digest
                ):
                    continue
                for artifact_id in attempt["payload"].get("artifact_ids") or []:
                    artifact = artifacts.get(artifact_id)
                    if (
                        artifact is None
                        or (artifact["payload"].get("metadata") or {}).get("role")
                        != "logic_entailment_certificate"
                        or not _artifact_intact(artifact)
                        or attempt["payload"].get("output_digest") != artifact["content_hash"]
                    ):
                        continue
                    try:
                        actual = json.loads(artifact["payload"]["content_text"])
                    except (TypeError, ValueError):
                        continue
                    if canonical_hash(actual) == canonical_hash(expected) and attempt[
                        "payload"
                    ].get("outcome") == ("passed" if expected["entailed"] else "failed"):
                        certificate_ok = True
                        certificate_attempt_ids.append(attempt["node_id"])

        entailed = certificate_ok and bool(expected and expected["entailed"])
        countermodel = expected["countermodel"] if certificate_ok and expected else None
        countermodel_reason = (
            f"A={str(countermodel['A']).lower()}, B={str(countermodel['B']).lower()} "
            "satisfies the frozen premises but not the conclusion. This refutes "
            "entailment, not the conclusion's truth in the actual world."
            if countermodel is not None
            else ""
        )
        criteria = (
            _criterion(
                "statement_identity",
                EvidenceAxisState.SATISFIED,
                "The exact ClaimRevision is frozen.",
                (closure.claim_revision_id,),
            ),
            _criterion(
                "premise_source_closure",
                EvidenceAxisState.SATISFIED if source_ok else EvidenceAxisState.UNDETERMINED,
                "Two intact premise Artifacts have matching frozen source references."
                if source_ok
                else "The two frozen, source-bound premises are missing or malformed.",
                tuple(item["artifact_id"] for item in premises),
            ),
            _criterion(
                "premise_validation",
                EvidenceAxisState.SATISFIED
                if premise_checks_ok
                else EvidenceAxisState.UNDETERMINED,
                "Each premise has its own system-derived successful check."
                if premise_checks_ok
                else "A premise lacks an intact system-derived check.",
                tuple(premise_attempt_ids),
            ),
            _criterion(
                "entailment_certificate",
                EvidenceAxisState.SATISFIED if certificate_ok else EvidenceAxisState.UNDETERMINED,
                "A system-derived certificate matches the independently recomputed truth table."
                if certificate_ok
                else "No exact checked derivation is available for this revision.",
                tuple(certificate_attempt_ids),
            ),
            _criterion(
                "logical_entailment",
                EvidenceAxisState.SATISFIED
                if entailed
                else EvidenceAxisState.FAILED
                if countermodel
                else EvidenceAxisState.UNDETERMINED,
                "Every premise-satisfying assignment satisfies the conclusion."
                if entailed
                else countermodel_reason or "Entailment has not been established.",
                tuple(certificate_attempt_ids),
            ),
        )
        if entailed:
            verdict = QualificationVerdict.ADMITTED
            blockers: tuple[str, ...] = ()
        elif countermodel:
            verdict = QualificationVerdict.BLOCKED
            blockers = ("premises_do_not_entail_claim",)
        else:
            verdict = QualificationVerdict.UNRESOLVED
            blockers = ("insufficient_entailment_evidence",)
        return QualificationDecision(
            verdict,
            criteria,
            blockers,
            {
                "statement_identity": EvidenceAxisState.SATISFIED,
                "artifact_integrity": EvidenceAxisState.SATISFIED
                if source_ok
                else EvidenceAxisState.UNDETERMINED,
                "local_correctness": EvidenceAxisState.SATISFIED
                if certificate_ok
                else EvidenceAxisState.UNDETERMINED,
                "logical_entailment": (
                    EvidenceAxisState.SATISFIED
                    if entailed
                    else EvidenceAxisState.FAILED
                    if countermodel
                    else EvidenceAxisState.UNDETERMINED
                ),
                "replayability": EvidenceAxisState.SATISFIED
                if certificate_ok
                else EvidenceAxisState.UNDETERMINED,
                "independent_validation": EvidenceAxisState.UNDETERMINED,
            },
        )
