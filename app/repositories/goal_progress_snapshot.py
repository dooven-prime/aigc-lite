"""Bounded, read-only source snapshot for one Goal progress evidence criterion.

``read_goal_progress_rows`` receives a query function tied to ONE read-only
database transaction.  It returns ``None`` when the Run is absent, otherwise::

    {
      "contract_version": "goal.progress.snapshot.v0",
      "tenant_id": str, "run_id": str,
      "root_claim_revision_id": str, "profile_id": str,
      "run": raw_agent_run_row,
      "goal_protocols": [raw_protocol_row],  # zero or one
      "subjects": {
        claim_revision_id: {
          "claim": decoded_claim_row_or_none,
          "sources": [decoded_linked_source_rows],
          "binding": raw_current_knowledge_binding_or_none,
          "receipts": [decoded_selected_qualification_receipt],
          "evaluations": [decoded_selected_evaluation],
          "admissions": [raw_selected_admission_receipt],
          "invalidations": [decoded_decisions_for_this_claim],
          "artifacts": [decoded_artifact_rows_from_selected_closure],
          "missing_references": [{"kind": str, "id": str}],
        }, ...
      },
    }

The selected rows are followed by exact IDs from the current knowledge
binding, then its receipt and evaluation.  Missing rows remain absent; this
loader does not infer qualification, authority, or currentness.  The caller
must compare every identity and hash.  Dependencies are followed only from
the frozen selected evaluation's ``dependency_binding`` nodes, under the same
profile.  No refresh method, write, or external request is performed.
"""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Callable
from typing import Any

Rows = Callable[[str, dict[str, Any]], list[dict[str, Any]]]

MAX_SNAPSHOT_ROWS = 2_000
MAX_SNAPSHOT_BYTES = 4_000_000
MAX_SUBJECTS = 64
MAX_DEPENDENCY_DEPTH = 16
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 20_000


class GoalProgressSnapshotError(ValueError):
    """The snapshot cannot safely be interpreted; no positive result follows."""


def collect_goal_progress_rows(source: Any, budget: dict[str, int]) -> list[dict[str, Any]]:
    """Materialize at most the shared v0 row and byte budget."""

    result: list[dict[str, Any]] = []
    for source_row in source:
        row = dict(source_row)
        budget["rows"] += 1
        budget["bytes"] += sum(
            len(str(key).encode("utf-8")) + (
                len(value) if isinstance(value, bytes)
                else len(str(value).encode("utf-8")) if value is not None else 0
            )
            for key, value in row.items()
        )
        if budget["rows"] > MAX_SNAPSHOT_ROWS or budget["bytes"] > MAX_SNAPSHOT_BYTES:
            raise GoalProgressSnapshotError("snapshot_too_large")
        result.append(row)
    return result


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GoalProgressSnapshotError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(_constant: str) -> None:
    raise GoalProgressSnapshotError("non_finite_json_number")


def _decode_json(value: Any, kind: type, field: str) -> Any:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            decoded = json.loads(
                value,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except GoalProgressSnapshotError:
            raise
        except (RecursionError, UnicodeError, ValueError) as exc:
            raise GoalProgressSnapshotError(f"malformed_json:{field}") from exc
    else:
        decoded = value
    if not isinstance(decoded, kind):
        raise GoalProgressSnapshotError(f"invalid_json_shape:{field}")

    count = 0
    pending = [(decoded, 0)]
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise GoalProgressSnapshotError(f"json_structure_too_large:{field}")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise GoalProgressSnapshotError(f"invalid_json_key:{field}")
                try:
                    key.encode("utf-8")
                except UnicodeError as exc:
                    raise GoalProgressSnapshotError(f"invalid_json_unicode:{field}") from exc
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise GoalProgressSnapshotError(f"non_finite_json_number:{field}")
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise GoalProgressSnapshotError(f"invalid_json_unicode:{field}") from exc
        elif item is not None and not isinstance(item, (bool, int)):
            raise GoalProgressSnapshotError(f"invalid_json_value:{field}")
    return decoded


def _decode_fields(
    row: dict[str, Any], fields: dict[str, type], prefix: str
) -> dict[str, Any]:
    result = dict(row)
    for field, kind in fields.items():
        if field not in result:
            raise GoalProgressSnapshotError(f"missing_field:{prefix}.{field}")
        result[field] = _decode_json(result[field], kind, f"{prefix}.{field}")
    return result


_CLAIM_JSON = {
    "definitions": list,
    "negative_boundaries": list,
    "dependency_claim_ids": list,
    "status_axes": dict,
    "blockers": list,
}
_RECEIPT_JSON = {
    "criteria": list,
    "blockers": list,
    "evidence_vector": dict,
    "independence_summary": dict,
}
_EVALUATION_JSON = {
    "profile_snapshot": dict,
    "evidence_closure": dict,
    **_RECEIPT_JSON,
}
_INVALIDATION_JSON = {
    "evidence_artifact_ids": list,
    "evidence_artifact_hashes": dict,
    "affected_binding_ids": list,
}


def _at_most_one(values: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if len(values) > 1:
        raise GoalProgressSnapshotError(f"multiple_rows:{label}")
    return values[0] if values else None


def _subject_rows(
    rows: Rows, tenant_id: str, claim_id: str, profile_id: str
) -> tuple[dict[str, Any], list[str]]:
    identity = {"tenant_id": tenant_id, "claim_id": claim_id, "profile_id": profile_id}
    claim = _at_most_one(rows(
        "SELECT * FROM research_claim_revisions WHERE tenant_id = :tenant_id "
        "AND id = :claim_id", identity,
    ), "claim")
    sources = rows(
        "SELECT s.* FROM research_claim_sources l JOIN research_sources s "
        "ON s.id = l.source_id AND s.tenant_id = l.tenant_id "
        "WHERE l.tenant_id = :tenant_id AND l.claim_revision_id = :claim_id "
        "ORDER BY s.source_key, s.locator, s.id", identity,
    ) if claim is not None else []
    binding = _at_most_one(rows(
        "SELECT * FROM current_use_bindings WHERE tenant_id = :tenant_id "
        "AND claim_revision_id = :claim_id AND profile_id = :profile_id "
        "AND use_scope = 'knowledge'", identity,
    ), "current_use_binding")
    receipt_rows: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []
    admission_rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    dependencies: list[str] = []
    missing_references: list[dict[str, str]] = []
    if claim is None:
        missing_references.append({"kind": "claim_revision", "id": claim_id})
    if binding is not None:
        selected = {"tenant_id": tenant_id, "receipt_id": binding["qualification_receipt_id"]}
        receipt_rows = rows(
            "SELECT * FROM qualification_receipts WHERE tenant_id = :tenant_id "
            "AND id = :receipt_id", selected,
        )
        receipt = _at_most_one(receipt_rows, "qualification_receipt")
        if receipt is None:
            missing_references.append({
                "kind": "qualification_receipt",
                "id": binding["qualification_receipt_id"],
            })
        admission_id = binding.get("knowledge_admission_receipt_id")
        if admission_id is not None:
            admission_rows = rows(
                "SELECT * FROM knowledge_admission_receipts WHERE tenant_id = :tenant_id "
                "AND id = :admission_id",
                {"tenant_id": tenant_id, "admission_id": admission_id},
            )
            _at_most_one(admission_rows, "knowledge_admission_receipt")
            if not admission_rows:
                missing_references.append({
                    "kind": "knowledge_admission_receipt", "id": admission_id,
                })
        if receipt is not None:
            evaluation_rows = rows(
                "SELECT * FROM qualification_evaluations WHERE tenant_id = :tenant_id "
                "AND id = :evaluation_id",
                {"tenant_id": tenant_id, "evaluation_id": receipt["evaluation_id"]},
            )
            evaluation = _at_most_one(evaluation_rows, "qualification_evaluation")
            if evaluation is None:
                missing_references.append({
                    "kind": "qualification_evaluation", "id": receipt["evaluation_id"],
                })
            if evaluation is not None:
                evaluation = _decode_fields(
                    evaluation, _EVALUATION_JSON, "qualification_evaluation"
                )
                evaluation_rows = [evaluation]
                closure_nodes = evaluation["evidence_closure"].get("nodes")
                if not isinstance(closure_nodes, list):
                    raise GoalProgressSnapshotError("invalid_closure_nodes")
                if len(closure_nodes) > MAX_JSON_NODES:
                    raise GoalProgressSnapshotError("closure_nodes_too_large")
                artifact_ids: set[str] = set()
                source_ids: set[str] = set()
                for node in closure_nodes:
                    if not isinstance(node, dict):
                        raise GoalProgressSnapshotError("invalid_closure_node")
                    node_type = node.get("node_type")
                    if node_type not in {"artifact", "source", "dependency_binding"}:
                        continue
                    node_id = node.get("node_id")
                    if not isinstance(node_id, str) or not 0 < len(node_id) <= 256:
                        raise GoalProgressSnapshotError("invalid_closure_node_id")
                    if node_type == "artifact":
                        artifact_ids.add(node_id)
                    elif node_type == "source":
                        source_ids.add(node_id)
                    else:
                        dependencies.append(node_id)
                linked_source_ids = {source["id"] for source in sources}
                missing_references.extend(
                    {"kind": "claim_source", "id": source_id}
                    for source_id in sorted(source_ids - linked_source_ids)
                )
                for artifact_id in sorted(artifact_ids):
                    item = rows(
                        "SELECT * FROM artifacts WHERE tenant_id = :tenant_id "
                        "AND id = :artifact_id",
                        {"tenant_id": tenant_id, "artifact_id": artifact_id},
                    )
                    artifact = _at_most_one(item, "artifact")
                    if artifact is not None:
                        artifacts.append(_decode_fields(artifact, {"metadata": dict}, "artifact"))
                    else:
                        missing_references.append({"kind": "artifact", "id": artifact_id})
    invalidations = rows(
        "SELECT * FROM invalidation_decisions WHERE tenant_id = :tenant_id "
        "AND target_claim_revision_id = :claim_id ORDER BY decided_at, id", identity,
    )
    return {
        "claim": _decode_fields(claim, _CLAIM_JSON, "claim") if claim else None,
        "sources": [
            _decode_fields(source, {"metadata": dict}, "source") for source in sources
        ],
        "binding": binding,
        "receipts": [
            _decode_fields(receipt, _RECEIPT_JSON, "qualification_receipt")
            for receipt in receipt_rows
        ],
        "evaluations": evaluation_rows,
        "admissions": admission_rows,
        "invalidations": [
            _decode_fields(item, _INVALIDATION_JSON, "invalidation")
            for item in invalidations
        ],
        "artifacts": artifacts,
        "missing_references": missing_references,
    }, dependencies


def read_goal_progress_rows(
    rows: Rows, tenant_id: str, run_id: str, claim_revision_id: str, profile_id: str
) -> dict[str, Any] | None:
    """Collect a narrow, recursively linked qualification view without writes."""

    if not all(
        isinstance(value, str) and 0 < len(value) <= 256
        for value in (tenant_id, run_id, claim_revision_id, profile_id)
    ):
        raise GoalProgressSnapshotError("invalid_snapshot_identity")
    run = _at_most_one(rows(
        "SELECT * FROM agent_runs WHERE tenant_id = :tenant_id AND id = :run_id",
        {"tenant_id": tenant_id, "run_id": run_id},
    ), "run")
    if run is None:
        return None
    if run.get("goal_json") is not None:
        _decode_json(run["goal_json"], dict, "run.goal_json")
    goal_protocols: list[dict[str, Any]] = []
    missing_references: list[dict[str, str]] = []
    protocol_id = run.get("goal_protocol_id")
    if protocol_id is not None:
        goal_protocols = rows(
            "SELECT * FROM evidence_protocols WHERE tenant_id = :tenant_id "
            "AND id = :protocol_id",
            {"tenant_id": tenant_id, "protocol_id": protocol_id},
        )
        _at_most_one(goal_protocols, "goal_protocol")
        if not goal_protocols:
            missing_references.append({"kind": "goal_protocol", "id": protocol_id})
        for protocol in goal_protocols:
            _decode_json(protocol["stop_conditions"], list, "protocol.stop_conditions")
            _decode_json(protocol["budget"], dict, "protocol.budget")
    subjects: dict[str, dict[str, Any]] = {}
    queue = deque([(claim_revision_id, 0)])
    while queue:
        claim_id, depth = queue.popleft()
        if claim_id in subjects:
            continue
        if depth > MAX_DEPENDENCY_DEPTH or len(subjects) >= MAX_SUBJECTS:
            raise GoalProgressSnapshotError("dependency_graph_too_large")
        subject, dependencies = _subject_rows(rows, tenant_id, claim_id, profile_id)
        subjects[claim_id] = subject
        for dependency_id in sorted(set(dependencies)):
            queue.append((dependency_id, depth + 1))
    return {
        "contract_version": "goal.progress.snapshot.v0",
        "tenant_id": tenant_id,
        "run_id": run_id,
        "root_claim_revision_id": claim_revision_id,
        "profile_id": profile_id,
        "run": run,
        "goal_protocols": goal_protocols,
        "missing_references": missing_references,
        "subjects": subjects,
    }
