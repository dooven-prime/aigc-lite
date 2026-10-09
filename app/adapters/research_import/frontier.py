"""AI Frontier registry parser; source-specific vocabulary stays outside research core."""

from __future__ import annotations

import hashlib
import json
import re

from ...core.errors import InvalidEvidenceError
from ...core.research import ClaimClosureStatus, ClaimRevisionDraft, SourceReferenceDraft
from ...profiles.frontier import (
    CAUSAL_STATUSES,
    CLAIM_TYPES,
    IMPORTER_VERSION,
    UNCERTAINTY_STATUSES,
)
from ...redaction import redact, redact_record_text, redact_text

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = frozenset(
    {
        "claim_id",
        "statement",
        "claim_type",
        "scope",
        "source_refs",
        "calculation_version",
        "uncertainty_status",
        "causal_status",
        "revision_status",
    }
)


class FrontierRegistryAdapter:
    """Normalize one bounded Frontier registry without granting epistemic authority."""

    importer_id = "frontier.registry"
    version = 1
    display_name = "AI Frontier Claim Registry"
    description = "Freeze a structurally validated Frontier claim registry and declared source hashes."
    source_format = "claim-registry.json; optional public-source-ledger.md"
    target_surface = "research_claim_candidate"
    preview_endpoint = None
    commit_endpoint = "/api/research-registry/import/frontier"
    importer_contract = IMPORTER_VERSION

    @staticmethod
    def _text(field: str, value: object, maximum: int) -> str:
        if not isinstance(value, str):
            raise InvalidEvidenceError(field, f"{field} must be a string")
        cleaned = redact_record_text(value.strip(), max_chars=maximum + 1)
        if not cleaned:
            raise InvalidEvidenceError(field, f"{field} is required")
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned

    @staticmethod
    def _optional_text(field: str, value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidEvidenceError(field, f"{field} must be a string")
        cleaned = redact_text(value.strip())
        if len(cleaned) > maximum:
            raise InvalidEvidenceError(field, f"{field} exceeds {maximum} characters")
        return cleaned or None

    @staticmethod
    def _hash(value: object) -> str:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(rendered).hexdigest()

    def normalize(self, payload: dict) -> dict:
        registry_id = self._text("registry_id", payload.get("registry_id"), 200)
        registry_version = self._text("registry_version", payload.get("registry_version"), 64)
        scope = payload.get("scope")
        if not isinstance(scope, dict):
            raise InvalidEvidenceError("scope", "scope must be an object")
        authority = self._text(
            "scope.authority", scope.get("authority") or "research_only", 100
        )
        as_of_date = self._optional_text("scope.as_of_date", scope.get("as_of_date"), 32)

        contract = payload.get("claim_contract")
        if not isinstance(contract, dict):
            raise InvalidEvidenceError("claim_contract", "claim_contract must be an object")
        required = contract.get("required_fields")
        if (
            not isinstance(required, list)
            or not all(isinstance(field, str) for field in required)
            or not _REQUIRED_FIELDS <= set(required)
        ):
            raise InvalidEvidenceError(
                "claim_contract.required_fields",
                "claim_contract does not include the required Frontier fields",
            )
        declared_types = contract.get("claim_types")
        if (
            not isinstance(declared_types, list)
            or not declared_types
            or not all(isinstance(claim_type, str) for claim_type in declared_types)
        ):
            raise InvalidEvidenceError(
                "claim_contract.claim_types", "claim_types must be a non-empty list"
            )

        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 2_000:
            raise InvalidEvidenceError("claims", "claims must contain between 1 and 2000 entries")
        seen_claims: set[str] = set()
        sources: dict[str, SourceReferenceDraft] = {}
        claims: list[ClaimRevisionDraft] = []
        for index, raw_claim in enumerate(raw_claims):
            field = f"claims[{index}]"
            if not isinstance(raw_claim, dict):
                raise InvalidEvidenceError(field, f"{field} must be an object")
            missing = _REQUIRED_FIELDS - set(raw_claim)
            if missing:
                raise InvalidEvidenceError(
                    field, f"{field} is missing {', '.join(sorted(missing))}"
                )
            claim_key = self._text(f"{field}.claim_id", raw_claim["claim_id"], 200)
            if claim_key in seen_claims:
                raise InvalidEvidenceError(f"{field}.claim_id", f"Duplicate claim id {claim_key}")
            seen_claims.add(claim_key)
            claim_type = self._text(f"{field}.claim_type", raw_claim["claim_type"], 100)
            if claim_type not in CLAIM_TYPES or claim_type not in declared_types:
                raise InvalidEvidenceError(f"{field}.claim_type", f"Unsupported claim type {claim_type}")
            uncertainty = self._text(
                f"{field}.uncertainty_status", raw_claim["uncertainty_status"], 100
            )
            if uncertainty not in UNCERTAINTY_STATUSES:
                raise InvalidEvidenceError(
                    f"{field}.uncertainty_status", f"Unsupported uncertainty status {uncertainty}"
                )
            causal = self._text(f"{field}.causal_status", raw_claim["causal_status"], 100)
            if causal not in CAUSAL_STATUSES:
                raise InvalidEvidenceError(
                    f"{field}.causal_status", f"Unsupported causal status {causal}"
                )
            revision_status = self._text(
                f"{field}.revision_status", raw_claim["revision_status"], 100
            )
            raw_sources = raw_claim["source_refs"]
            if not isinstance(raw_sources, list) or len(raw_sources) > 100:
                raise InvalidEvidenceError(
                    f"{field}.source_refs", "source_refs must be a list of at most 100 entries"
                )
            source_ref_keys = []
            for source_index, raw_source in enumerate(raw_sources):
                source_field = f"{field}.source_refs[{source_index}]"
                if not isinstance(raw_source, dict):
                    raise InvalidEvidenceError(source_field, f"{source_field} must be an object")
                source_key = self._text(
                    f"{source_field}.source_id", raw_source.get("source_id"), 200
                )
                locator = self._text(
                    f"{source_field}.locator", raw_source.get("locator"), 4_000
                )
                content_hash = self._text(
                    f"{source_field}.sha256", raw_source.get("sha256"), 64
                ).lower()
                if not _SHA256.fullmatch(content_hash):
                    raise InvalidEvidenceError(
                        f"{source_field}.sha256", "source sha256 must be a SHA-256 digest"
                    )
                ref_key = self._hash(
                    {
                        "source_key": source_key,
                        "locator": locator,
                        "content_hash": content_hash,
                    }
                )
                source_ref_keys.append(ref_key)
                sources.setdefault(
                    ref_key,
                    SourceReferenceDraft(
                        ref_key=ref_key,
                        source_key=source_key,
                        locator=locator,
                        content_hash=content_hash,
                        metadata={"declared_by_registry": registry_id},
                    ),
                )
            blockers = []
            if not source_ref_keys:
                blockers.append("source_refs_missing")
            if revision_status != "current":
                blockers.append(f"revision_status:{revision_status}")
            closure_status = (
                ClaimClosureStatus.BLOCKED if blockers else ClaimClosureStatus.CLOSED
            )
            claims.append(
                ClaimRevisionDraft(
                    claim_key=claim_key,
                    statement=self._text(f"{field}.statement", raw_claim["statement"], 8_000),
                    claim_type=claim_type,
                    scope=self._text(f"{field}.scope", raw_claim["scope"], 8_000),
                    method_revision=self._text(
                        f"{field}.calculation_version", raw_claim["calculation_version"], 200
                    ),
                    lifecycle_status=revision_status,
                    closure_status=closure_status,
                    status_axes={"uncertainty": uncertainty, "causal": causal},
                    blockers=tuple(blockers),
                    source_ref_keys=tuple(source_ref_keys),
                )
            )
        return {
            "registry_id": registry_id,
            "registry_version": registry_version,
            "authority": authority,
            "as_of_date": as_of_date,
            "claim_contract": redact(contract),
            "sources": list(sources.values()),
            "claims": claims,
        }
