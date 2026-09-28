"""Read-only decision evaluation profile and NanoJev bundle importer."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable

from ..core.artifacts import ArtifactDraft, ArtifactKind
from ..core.contracts import RequestContext
from ..core.errors import InvalidEvidenceError, ResourceNotFoundError
from ..core.evidence import (
    ClaimDraft,
    EvidenceResolution,
    ExecutionReceiptDraft,
    FreezeManifestDraft,
    ProtocolDraft,
    ProtocolStatus,
    ReviewDraft,
    ReviewerKind,
    ReviewStatus,
)
from ..database import get_repository
from ..profiles.decision import DecisionCaseDraft, DecisionResolution
from ..redaction import redact
from ..repository import Repository
from .artifacts import ArtifactService
from .evidence import EvidenceService

RepositoryProvider = Callable[[], Repository]
_FAMILY_SUFFIX = re.compile(r"_\d+$")
_QUESTION_TYPES = {"boolean", "choice", "score"}


class DecisionLabService:
    def __init__(
        self,
        repository_provider: RepositoryProvider = get_repository,
        *,
        artifact_service: ArtifactService | None = None,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._artifacts = artifact_service or ArtifactService(
            repository_provider=repository_provider
        )
        self._evidence = evidence_service or EvidenceService(repository_provider)

    def import_nanojev(
        self,
        context: RequestContext,
        *,
        source_name: str,
        request_bytes: bytes,
        predictions_bytes: bytes,
        metrics_bytes: bytes,
        receipt_bytes: bytes | None = None,
        confidence_threshold: float = 0.7,
    ) -> dict:
        if not 0 <= confidence_threshold <= 1:
            raise InvalidEvidenceError(
                "confidence_threshold", "confidence_threshold must be between 0 and 1"
            )
        source_name = source_name.strip() or "NanoJev evaluation"
        if len(source_name) > 200:
            raise InvalidEvidenceError("source_name", "source_name exceeds 200 characters")
        request_payload = self._json(request_bytes, "request_file")
        predictions_payload = self._json(predictions_bytes, "predictions_file")
        metrics_payload = self._json(metrics_bytes, "metrics_file")
        cases = self._cases(
            request_payload,
            predictions_payload,
            confidence_threshold=confidence_threshold,
        )
        self._validate_metrics(
            metrics_payload, sum(len(case.questions) for case in cases)
        )

        members = [
            self._source_member("request.json", request_bytes),
            self._source_member("predictions.json", predictions_bytes),
            self._source_member("metrics.json", metrics_bytes),
        ]
        if receipt_bytes:
            members.append(self._source_member("receipt.yaml", receipt_bytes))
        bundle_hash = self._manifest_hash(
            [
                *members,
                {
                    "name": "import-policy",
                    "confidence_threshold": confidence_threshold,
                },
            ]
        )
        repository = self._repository_provider()
        existing = repository.get_evidence_protocol_by_hash(
            context.workspace_id, "decision.nanojev", bundle_hash
        )
        if existing is not None:
            return {**self.dashboard(context, protocol_id=existing["id"]), "imported": False}

        protocol = self._evidence.create_protocol(
            context,
            ProtocolDraft(
                name=source_name,
                profile="decision.nanojev",
                purpose="Evaluate a frozen dynamic-candidate decision interface.",
                scope=(
                    "Imported evaluation bundle only. Model probabilities are observations, "
                    "not authority to perform a business action."
                ),
                completion_predicate=(
                    "Every request case has a schema-compatible prediction and the imported "
                    "metrics count matches the frozen cohort."
                ),
                stop_conditions=(
                    "Schema mismatch or missing case",
                    "Invalid or non-normalized probability distribution",
                    "Uploaded artifact exceeds the configured import boundary",
                ),
                budget={"max_cases": 1_000, "network_model_calls": 0},
                status=ProtocolStatus.FROZEN,
            ),
            content_hash=bundle_hash,
        )

        artifacts = [
            self._artifact(
                context,
                protocol["id"],
                f"{source_name} request.json",
                "application/json",
                request_payload,
                role="request",
            ),
            self._artifact(
                context,
                protocol["id"],
                f"{source_name} predictions.json",
                "application/json",
                predictions_payload,
                role="predictions",
            ),
            self._artifact(
                context,
                protocol["id"],
                f"{source_name} metrics.json",
                "application/json",
                metrics_payload,
                role="metrics",
            ),
        ]
        if receipt_bytes:
            artifacts.append(
                self._artifacts.create_artifact(
                    context,
                    ArtifactDraft(
                        name=f"{source_name} receipt.yaml",
                        kind=ArtifactKind.TEXT,
                        media_type="application/yaml",
                        content_text=receipt_bytes.decode("utf-8", errors="replace"),
                        metadata={
                            "profile": "decision.nanojev",
                            "protocol_id": protocol["id"],
                            "role": "source_receipt",
                        },
                    ),
                )
            )

        input_digest = hashlib.sha256(request_bytes).hexdigest()
        output_digest = hashlib.sha256(predictions_bytes).hexdigest()
        execution = predictions_payload.get("execution")
        checkpoint = predictions_payload.get("checkpoint")
        runtime = {
            "execution": execution if isinstance(execution, dict) else {},
            "checkpoint": checkpoint if isinstance(checkpoint, dict) else {},
            "network_model_calls": 0,
        }
        receipt = self._evidence.create_receipt(
            context,
            protocol["id"],
            ExecutionReceiptDraft(
                status=str(metrics_payload.get("status") or "imported").lower(),
                input_digest=input_digest,
                output_digest=output_digest,
                runtime=runtime,
                budget={"cases": len(cases)},
                artifact_ids=tuple(item["id"] for item in artifacts),
                metadata={
                    "source": "nanojev",
                    "confidence_threshold": confidence_threshold,
                    "metrics": self._metrics_summary(metrics_payload),
                },
            ),
        )
        claim = self._evidence.create_claim(
            context,
            protocol["id"],
            ClaimDraft(
                statement=(
                    f"The imported {source_name} bundle contains {len(cases)} aligned "
                    "request/prediction cases and completed local schema validation."
                ),
                resolution=EvidenceResolution.SUPPORTED,
                scope="Bundle integrity and schema alignment at import time.",
                evidence_refs=tuple(item["id"] for item in artifacts),
                prohibited_upgrades=(
                    "Do not infer production readiness from this import.",
                    "Do not treat probability or argmax as business authority.",
                    "Do not treat local validation as independent scientific review.",
                ),
            ),
        )
        self._evidence.create_review(
            context,
            receipt["id"],
            ReviewDraft(
                status=ReviewStatus.ACCEPTED,
                reviewer_kind=ReviewerKind.AUTOMATED,
                finding=(
                    "Request/prediction ids, question types, candidates, probability "
                    "normalization, and aggregate count passed import validation. This is "
                    "not an independent quality review."
                ),
                reviewer_id="aigc-lite.nanojev-importer",
                independent=False,
                evidence_refs=(claim["id"],),
            ),
        )
        stored_members = tuple(
            {
                "name": artifact["name"],
                "artifact_id": artifact["id"],
                "sha256": artifact["content_hash"],
                "size_bytes": artifact["size_bytes"],
            }
            for artifact in artifacts
        )
        self._evidence.create_freeze(
            context,
            protocol["id"],
            FreezeManifestDraft(
                name=f"{source_name} import freeze",
                version=1,
                members=stored_members,
                content_hash=self._manifest_hash(
                    [
                        {
                            "name": item["name"],
                            "sha256": item["sha256"],
                            "size_bytes": item["size_bytes"],
                        }
                        for item in stored_members
                    ]
                ),
            ),
        )
        primary_artifact_id = artifacts[1]["id"]
        repository.create_decision_cases(
            context.workspace_id,
            protocol["id"],
            receipt["id"],
            [
                {
                    "source_case_id": case.source_case_id,
                    "family": case.family,
                    "state_text": case.state_text,
                    "questions": case.questions,
                    "answers": case.answers,
                    "resolution": case.resolution.value,
                    "confidence": case.confidence,
                    "entropy": case.entropy,
                    "threshold": case.threshold,
                    "run_id": case.run_id,
                    "step_id": case.step_id,
                    "primary_artifact_id": primary_artifact_id,
                }
                for case in cases
            ],
        )
        return {**self.dashboard(context, protocol_id=protocol["id"]), "imported": True}

    def dashboard(
        self,
        context: RequestContext,
        *,
        protocol_id: str | None = None,
        family: str | None = None,
        resolution: str | None = None,
        limit: int = 500,
        include_gold: bool = False,
    ) -> dict:
        protocols = self._evidence.list_protocols(
            context, profile="decision.nanojev", limit=100
        )
        if protocol_id is None:
            protocol_id = protocols[0]["id"] if protocols else None
        if protocol_id is None:
            return {
                "protocols": [],
                "protocol": None,
                "statistics": self._statistics([]),
                "cases": [],
            }
        if not any(item["id"] == protocol_id for item in protocols):
            raise ResourceNotFoundError("decision_protocol", protocol_id)
        protocol = self._evidence.get_protocol(context, protocol_id)
        cases = self._repository_provider().list_decision_cases(
            context.workspace_id,
            protocol_id=protocol_id,
            family=family,
            resolution=resolution,
            limit=limit,
        )
        if not include_gold:
            for case in cases:
                case.pop("gold_answers", None)
        return {
            "protocols": protocols,
            "protocol": self._protocol_projection(protocol),
            "statistics": self._statistics(cases),
            "cases": cases,
        }

    def get_case(
        self, context: RequestContext, case_id: str, *, include_gold: bool = False
    ) -> dict:
        case = self._repository_provider().get_decision_case(
            context.workspace_id, case_id
        )
        if case is None:
            raise ResourceNotFoundError("decision_case", case_id)
        if not include_gold:
            case.pop("gold_answers", None)
        return case

    def _artifact(
        self,
        context: RequestContext,
        protocol_id: str,
        name: str,
        media_type: str,
        payload: dict,
        *,
        role: str,
    ) -> dict:
        return self._artifacts.create_artifact(
            context,
            ArtifactDraft(
                name=name,
                kind=ArtifactKind.JSON,
                media_type=media_type,
                content_text=json.dumps(payload, ensure_ascii=False, indent=2),
                metadata={
                    "profile": "decision.nanojev",
                    "protocol_id": protocol_id,
                    "role": role,
                },
            ),
        )

    @classmethod
    def _cases(
        cls,
        request_payload: dict,
        predictions_payload: dict,
        *,
        confidence_threshold: float,
    ) -> list[DecisionCaseDraft]:
        request_states = cls._state_list(request_payload, "request_file")
        prediction_states = cls._state_list(predictions_payload, "predictions_file")
        requests = cls._index(request_states, "request_file")
        predictions = cls._index(prediction_states, "predictions_file")
        if requests.keys() != predictions.keys():
            missing = sorted(requests.keys() - predictions.keys())[:5]
            extra = sorted(predictions.keys() - requests.keys())[:5]
            raise InvalidEvidenceError(
                "states",
                f"Request/prediction case ids differ; missing={missing}, extra={extra}",
            )
        cases = []
        for case_id, request in requests.items():
            state_text = request.get("state")
            questions = request.get("questions")
            answers = predictions[case_id].get("answers")
            if not isinstance(state_text, str) or not state_text.strip():
                raise InvalidEvidenceError("state", f"Case {case_id} has no state text")
            if not isinstance(questions, dict) or not questions:
                raise InvalidEvidenceError("questions", f"Case {case_id} has no questions")
            if not isinstance(answers, dict) or answers.keys() != questions.keys():
                raise InvalidEvidenceError(
                    "answers", f"Case {case_id} answer keys do not match questions"
                )
            confidences: list[float] = []
            entropies: list[float] = []
            for question_id, question in questions.items():
                if not isinstance(question, dict):
                    raise InvalidEvidenceError(
                        "questions", f"Case {case_id}/{question_id} must be an object"
                    )
                question_type = question.get("type")
                if question_type not in _QUESTION_TYPES:
                    raise InvalidEvidenceError(
                        "question.type",
                        f"Case {case_id}/{question_id} has unsupported type",
                    )
                candidates = cls._candidates(question, case_id, question_id)
                answer = answers[question_id]
                if not isinstance(answer, dict) or answer.get("type") != question_type:
                    raise InvalidEvidenceError(
                        "answer.type", f"Case {case_id}/{question_id} type mismatch"
                    )
                probabilities = answer.get("probabilities")
                if not isinstance(probabilities, dict) or set(probabilities) != set(candidates):
                    raise InvalidEvidenceError(
                        "probabilities",
                        f"Case {case_id}/{question_id} candidate keys do not match",
                    )
                values = []
                for candidate in candidates:
                    probability = probabilities[candidate]
                    if (
                        isinstance(probability, bool)
                        or not isinstance(probability, (int, float))
                        or not math.isfinite(probability)
                        or not 0 <= probability <= 1
                    ):
                        raise InvalidEvidenceError(
                            "probabilities",
                            f"Case {case_id}/{question_id} has an invalid probability",
                        )
                    values.append(float(probability))
                if not math.isclose(sum(values), 1.0, rel_tol=0, abs_tol=1e-4):
                    raise InvalidEvidenceError(
                        "probabilities",
                        f"Case {case_id}/{question_id} probabilities do not sum to one",
                    )
                confidences.append(max(values))
                entropies.append(cls._normalized_entropy(values))
            confidence = min(confidences)
            cases.append(
                DecisionCaseDraft(
                    source_case_id=case_id,
                    family=_FAMILY_SUFFIX.sub("", case_id),
                    state_text=str(redact(state_text)),
                    questions=redact(questions),
                    answers=redact(answers),
                    resolution=(
                        DecisionResolution.DECIDED
                        if confidence >= confidence_threshold
                        else DecisionResolution.MANUAL_REVIEW
                    ),
                    confidence=confidence,
                    entropy=max(entropies),
                    threshold=confidence_threshold,
                )
            )
        return cases

    @staticmethod
    def _json(content: bytes, field: str) -> dict:
        try:
            value = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidEvidenceError(field, f"{field} must contain UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise InvalidEvidenceError(field, f"{field} must contain a JSON object")
        return value

    @staticmethod
    def _state_list(payload: dict, field: str) -> list[dict]:
        states = payload.get("states")
        if not isinstance(states, list) or not 1 <= len(states) <= 1_000:
            raise InvalidEvidenceError(
                field, f"{field}.states must contain between 1 and 1000 cases"
            )
        if not all(isinstance(item, dict) for item in states):
            raise InvalidEvidenceError(field, f"{field}.states entries must be objects")
        return states

    @staticmethod
    def _index(states: list[dict], field: str) -> dict[str, dict]:
        result = {}
        for state in states:
            case_id = state.get("id")
            if not isinstance(case_id, str) or not case_id or len(case_id) > 200:
                raise InvalidEvidenceError(field, f"{field} contains an invalid case id")
            if case_id in result:
                raise InvalidEvidenceError(field, f"{field} contains duplicate id {case_id}")
            result[case_id] = state
        return result

    @staticmethod
    def _candidates(question: dict, case_id: str, question_id: str) -> list[str]:
        criteria = question.get("criteria")
        if isinstance(criteria, dict) and criteria:
            return [str(item) for item in criteria]
        if isinstance(criteria, list) and criteria:
            return [str(index) for index in range(len(criteria))]
        raise InvalidEvidenceError(
            "criteria", f"Case {case_id}/{question_id} has no candidates"
        )

    @staticmethod
    def _normalized_entropy(values: list[float]) -> float:
        if len(values) <= 1:
            return 0.0
        entropy = -sum(value * math.log(value) for value in values if value > 0)
        return min(1.0, max(0.0, entropy / math.log(len(values))))

    @staticmethod
    def _validate_metrics(metrics: dict, case_count: int) -> None:
        predicted = metrics.get("predicted_questions")
        if predicted is not None and predicted != case_count:
            raise InvalidEvidenceError(
                "metrics_file",
                "metrics predicted_questions does not match imported case count",
            )

    @staticmethod
    def _metrics_summary(metrics: dict) -> dict:
        allowed = {
            "status",
            "gold_questions",
            "predicted_questions",
            "missing_questions",
            "correct_count",
            "error_count",
            "accuracy",
            "macro_family_accuracy",
            "nll",
            "brier_multiclass",
            "ece_10",
            "boolean",
            "choice_accuracy",
            "score_expected_mae",
            "score_argmax_accuracy",
            "confidence_thresholds",
            "family_metrics",
            "type_metrics",
        }
        return redact({key: value for key, value in metrics.items() if key in allowed})

    @staticmethod
    def _source_member(name: str, content: bytes) -> dict:
        return {
            "name": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    @staticmethod
    def _manifest_hash(members: list[dict]) -> str:
        payload = json.dumps(
            members, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _protocol_projection(protocol: dict) -> dict:
        value = dict(protocol)
        value["artifacts"] = [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "name",
                    "kind",
                    "media_type",
                    "content_hash",
                    "size_bytes",
                    "metadata",
                    "created_at",
                )
            }
            | {"preview": (item.get("content_text") or "")[:4_000]}
            for item in protocol.get("artifacts", [])
        ]
        return value

    @staticmethod
    def _statistics(cases: list[dict]) -> dict:
        resolutions = Counter(item["resolution"] for item in cases)
        families = Counter(item["family"] for item in cases)
        question_types: Counter[str] = Counter()
        for item in cases:
            for question in item.get("questions", {}).values():
                if isinstance(question, dict) and isinstance(question.get("type"), str):
                    question_types[question["type"]] += 1
        return {
            "cases": len(cases),
            "decided": resolutions["decided"],
            "manual_review": resolutions["manual_review"],
            "undetermined": resolutions["undetermined"],
            "mean_confidence": (
                sum(item["confidence"] for item in cases) / len(cases) if cases else 0
            ),
            "mean_entropy": (
                sum(item["entropy"] for item in cases) / len(cases) if cases else 0
            ),
            "families": dict(sorted(families.items())),
            "question_types": dict(sorted(question_types.items())),
        }
