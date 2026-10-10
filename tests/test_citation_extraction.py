"""Citation candidates cannot silently become knowledge or authority."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.citation_extraction import create_citation_extraction_router
from app.auth import current_admin_user
from app.core.contracts import RequestContext
from app.core.errors import InvalidArtifactError
from app.profiles.math_theorem import PROFILE_ID
from app.repository import SQLiteRepository
from app.services.artifacts import ArtifactService
from app.services.citation_extraction import CitationExtractionService
from app.services.qualification import QualificationService
from scripts.citation_blind_eval import _check_anchor, freeze, run, score, seal
from scripts.citation_gold_annotator import GoldDraft, find_text_matches

URI = "https://example.org/frozen-paper.pdf"
CONTEXT = RequestContext("request-1", "workspace-a", "admin-a")


def _input_occurrence(value: dict) -> dict:
    return {key: value[key] for key in ("id", "anchor", "style")}


def _input_match(value: dict) -> dict:
    return {
        key: value[key] for key in ("id", "occurrence_id", "reference_anchor", "target_identifier")
    }


def _pdf(lines: list[str]) -> bytes:
    """Generate a tiny extractable PDF without a fixture-generation dependency."""
    commands = ["BT /F1 12 Tf 40 750 Td 15 TL"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"{'T* ' if index else ''}({escaped}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def _setup(tmp_path):
    path = tmp_path / "citation.db"
    repository = SQLiteRepository(path)
    repository.init()
    service = CitationExtractionService(ArtifactService(repository_provider=lambda: repository))
    return path, repository, service


def _counts(path):
    with sqlite3.connect(path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "qualification_receipts",
                "current_use_bindings",
                "authorization_grants",
                "research_claim_relations",
            )
        }


def test_preview_commit_records_candidates_only_and_preserves_authority(tmp_path) -> None:
    path, repository, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study. arXiv:1234.56789."])
    before = _counts(path)
    preview = service.preview(CONTEXT, pdf, URI)
    assert preview["admission_state"] == "candidate_only"
    assert preview["occurrences"][0]["anchor"]["quote"] == "[1]"
    assert preview["bibliographic_matches"][0]["status"] == "reference_anchored"
    assert preview["bibliographic_matches"][0]["target_identifier"] == "arxiv:1234.56789"
    result = service.commit(CONTEXT, pdf, URI, preview["preview_hash"])
    stored = repository.get_artifact(CONTEXT.workspace_id, result["artifact_id"])
    bundle = json.loads(stored["content_text"])
    assert stored["content_hash"] == preview["preview_hash"]
    assert bundle["source_snapshot"]["pdf_sha256"] == result["pdf_sha256"]
    assert bundle["proposal"]["bibliographic_matches"][0]["status"] == "reference_anchored"
    assert _counts(path) == before
    assert (
        QualificationService(lambda: repository).qualified_search(CONTEXT, "Study", PROFILE_ID)
        == []
    )
    assert all(
        result[key] is None
        for key in ("qualification_receipt_id", "current_use_binding_id", "authorization_grant_id")
    )
    with pytest.raises(InvalidArtifactError):
        service.commit(CONTEXT, pdf, URI, "0" * 64)
    assert len(repository.list_artifacts(CONTEXT.workspace_id, 20)) == 1


def test_forged_occurrence_rejected_and_wrong_bibliography_abstains(tmp_path) -> None:
    path, repository, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study. arXiv:1234.56789."])
    baseline = service.preview(CONTEXT, pdf, URI)
    occurrence = _input_occurrence(baseline["occurrences"][0])
    match = baseline["bibliographic_matches"][0]
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "bibliographic_matches": [
            {
                **{
                    key: match[key]
                    for key in ("id", "occurrence_id", "reference_anchor", "target_identifier")
                },
                "target_identifier": "arXiv:9999.99999",
            }
        ],
        "claim_relation_proposals": [],
        "proposer_model_route": "self-reported-model",
    }
    preview = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert preview["bibliographic_matches"][0]["status"] == "abstained"
    assert preview["truncated"] is None
    assert preview["bibliographic_matches"][0]["target_identifier"] is None
    assert preview["bibliographic_matches"][0]["submitted_target_identifier"] == "arXiv:9999.99999"
    service.commit(CONTEXT, pdf, URI, preview["preview_hash"], json.dumps(proposal))
    assert _counts(path) == {key: 0 for key in _counts(path)}
    proposal["occurrences"][0]["anchor"]["quote"] = "[2]"
    with pytest.raises(InvalidArtifactError, match="Anchor quote"):
        service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert len(repository.list_artifacts(CONTEXT.workspace_id, 20)) == 1


def test_reference_anchor_cannot_be_forged_from_body_text(tmp_path) -> None:
    _, _, service = _setup(tmp_path)
    pdf = _pdf(
        [
            "A claim [1].",
            "[1] Doe. Untrusted body text. arXiv:1234.56789.",
            "References",
            "[1] Roe. Actual entry. arXiv:8888.88888.",
        ]
    )
    baseline = service.preview(CONTEXT, pdf, URI)
    snapshot = service._snapshot(pdf, URI)
    text = snapshot["pages"][0]["text"]
    reference_like = next(
        item
        for item in baseline["occurrences"]
        if item["anchor"]["start"] == text.index("[1] Doe.")
    )
    assert reference_like["status"] == "abstained"
    assert reference_like["reason"] == "reference_like_line"
    occurrence = _input_occurrence(
        next(item for item in baseline["occurrences"] if item["anchor"]["quote"] == "[1]")
    )
    start = text.index("[1] Doe.")
    end = text.index("\n", start)
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "bibliographic_matches": [
            {
                "id": "b-1",
                "occurrence_id": occurrence["id"],
                "reference_anchor": {
                    "page": 1,
                    "start": start,
                    "end": end,
                    "quote": text[start:end],
                },
                "target_identifier": "arXiv:1234.56789",
            }
        ],
    }
    preview = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert preview["bibliographic_matches"][0]["status"] == "abstained"


@pytest.mark.parametrize(
    ("actual", "truncated"),
    [
        ("arXiv:1607.06450", "arXiv:1607.0645"),
        ("10.1234/alpha.beta", "10.1234/alpha"),
    ],
)
def test_identifier_prefix_is_not_exact_match(tmp_path, actual: str, truncated: str) -> None:
    _, _, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", f"[1] Doe. Study. {actual}."])
    baseline = service.preview(CONTEXT, pdf, URI)
    assert baseline["bibliographic_matches"][0]["status"] == "reference_anchored"
    occurrence = _input_occurrence(baseline["occurrences"][0])
    match = _input_match(baseline["bibliographic_matches"][0])
    match["target_identifier"] = truncated
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "bibliographic_matches": [match],
    }
    rejected = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert rejected["bibliographic_matches"][0]["status"] == "abstained"
    assert rejected["bibliographic_matches"][0]["target_identifier"] is None
    assert rejected["bibliographic_matches"][0]["submitted_target_identifier"] == truncated
    match["target_identifier"] = actual
    accepted = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert accepted["bibliographic_matches"][0]["status"] == "reference_anchored"


def test_reference_list_marker_cannot_be_a_body_occurrence(tmp_path) -> None:
    _, _, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study. arXiv:1607.06450."])
    baseline = service.preview(CONTEXT, pdf, URI)
    reference = baseline["bibliographic_matches"][0]["reference_anchor"]
    occurrence = {
        "id": "o-ref",
        "style": "numeric",
        "anchor": {
            "page": reference["page"],
            "start": reference["start"],
            "end": reference["start"] + 3,
            "quote": "[1]",
        },
    }
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "bibliographic_matches": [
            {
                "id": "b-ref",
                "occurrence_id": "o-ref",
                "reference_anchor": reference,
                "target_identifier": "arXiv:1607.06450",
            }
        ],
    }
    preview = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert preview["occurrences"][0]["status"] == "abstained"
    assert preview["occurrences"][0]["reason"] == "outside_body_region"
    assert preview["bibliographic_matches"][0]["status"] == "abstained"


def test_unknown_body_boundary_abstains(tmp_path) -> None:
    _, _, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "[1] Doe. Study."])
    preview = service.preview(CONTEXT, pdf, URI)
    assert preview["occurrences"][0]["status"] == "abstained"
    assert preview["occurrences"][0]["reason"] == "body_region_unconfirmed"


def test_duplicate_match_only_one_effective_reference(tmp_path) -> None:
    path, _, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study. arXiv:1607.06450."])
    baseline = service.preview(CONTEXT, pdf, URI)
    occurrence = _input_occurrence(baseline["occurrences"][0])
    first = _input_match(baseline["bibliographic_matches"][0])
    second = {**first, "id": "b-second"}
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "bibliographic_matches": [first, second],
    }
    preview = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert [value["status"] for value in preview["bibliographic_matches"]] == [
        "reference_anchored",
        "abstained",
    ]
    assert preview["bibliographic_matches"][1]["reason"] == "duplicate_occurrence_match"
    service.commit(CONTEXT, pdf, URI, preview["preview_hash"], json.dumps(proposal))
    assert _counts(path) == {key: 0 for key in _counts(path)}


def test_baseline_reports_truncation_at_occurrence_limit(tmp_path) -> None:
    _, _, service = _setup(tmp_path)
    exact_limit_pdf = _pdf([" ".join(["[1]"] * 200), "References", "[1] Doe. Study."])
    exact_limit = service.preview(CONTEXT, exact_limit_pdf, URI)
    assert len(exact_limit["occurrences"]) == 200
    assert exact_limit["truncated"] is False
    pdf = _pdf([" ".join(["[1]"] * 201), "References", "[1] Doe. Study."])
    preview = service.preview(CONTEXT, pdf, URI)
    assert len(preview["occurrences"]) == 200
    assert preview["occurrence_limit"] == 200
    assert preview["truncated"] is True


def test_blind_eval_seals_human_gold_before_running_and_scores_layers(tmp_path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = _pdf(
        [
            "A claim [1] and a value [2].",
            "References",
            "[1] Doe. Study. arXiv:1234.56789.",
            "[2] Roe. Other study.",
        ]
    )
    (pdf_dir / "heldout.pdf").write_bytes(pdf)
    spec = {
        "schema": "citation-blind-eval.v1",
        "not_used_for_development": True,
        "papers": [{"paper_id": "heldout", "source_uri": URI, "stratum": "synthetic-source"}],
    }
    manifest = freeze(spec, pdf_dir)
    with pytest.raises(ValueError, match="sealed gold lock"):
        run(manifest, {}, pdf_dir)
    snapshot = CitationExtractionService._snapshot(pdf, URI)
    text = snapshot["pages"][0]["text"]
    baseline = CitationExtractionService._baseline(snapshot["pages"])[0]
    reference = CitationExtractionService._reference_entries(snapshot["pages"])

    def anchor(quote: str) -> dict:
        start = text.index(quote)
        return {"page": 1, "start": start, "end": start + len(quote), "quote": quote}

    gold = {
        "schema": "citation-blind-eval.v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "annotator_id": "human-annotator-1",
        "annotation_protocol": "visual-full-paper-v1",
        "papers": [
            {
                "paper_id": "heldout",
                "exhaustive": True,
                "occurrences": [
                    {
                        "page": 1,
                        "quote": "[1]",
                        "style": "numeric",
                        "anchor": anchor("[1]"),
                        "reference_anchor": reference["1"][0],
                        "target_identifier": "arxiv:1234.56789",
                    },
                    {
                        "page": 1,
                        "quote": "[2]",
                        "style": "numeric",
                        "anchor": anchor("[2]"),
                        "reference_anchor": reference["2"][0],
                        "target_identifier": None,
                    },
                ],
            }
        ],
    }
    assert len(baseline.occurrences) == 2
    negative_gold = json.loads(json.dumps(gold))
    negative_quote = text[-3:-1]
    negative_gold["papers"][0]["occurrences"][0].update(
        quote=negative_quote,
        anchor={"page": 1, "start": -3, "end": -1, "quote": negative_quote},
    )
    with pytest.raises(ValueError, match="outside frozen"):
        seal(manifest, negative_gold, pdf_dir)
    overrun_gold = json.loads(json.dumps(gold))
    overrun_quote = text[1:]
    overrun_gold["papers"][0]["occurrences"][0].update(
        quote=overrun_quote,
        anchor={"page": 1, "start": 1, "end": len(text) + 99, "quote": overrun_quote},
    )
    with pytest.raises(ValueError, match="outside frozen"):
        seal(manifest, overrun_gold, pdf_dir)
    lock = seal(manifest, gold, pdf_dir)
    predictions = run(manifest, lock, pdf_dir)
    report = score(manifest, lock, gold, predictions, pdf_dir)
    assert report["occurrence_precision"] == 1
    assert report["occurrence_recall"] == 1
    assert report["by_citation_style"]["numeric"]["precision"] == 1
    assert report["by_citation_style"]["numeric"]["recall"] == 1
    assert report["by_citation_style"]["numeric"]["gold_occurrence_count"] == 2
    assert report["by_citation_style"]["numeric"]["gold_paper_count"] == 1
    assert report["by_source_stratum"]["synthetic-source"]["recall"] == 1
    assert report["by_source_stratum"]["synthetic-source"]["paper_count"] == 1
    assert report["by_source_stratum"]["synthetic-source"]["gold_occurrence_count"] == 2
    assert report["reference_entry_precision"] == 1
    assert report["bibliographic_identity_precision"] == 1
    assert report["counts"]["identity_eligible"] == 1
    assert report["annotation_cost_usd"] is None
    assert report["model_spend_usd"] == 0
    no_identifiers = json.loads(json.dumps(predictions))
    for match in no_identifiers["papers"][0]["bibliographic_matches"]:
        match["target_identifier"] = None
    with pytest.raises(ValueError, match="does not reproduce"):
        score(manifest, lock, gold, no_identifiers, pdf_dir)
    wrong_reference = json.loads(json.dumps(predictions))
    wrong_reference["papers"][0]["bibliographic_matches"][0]["reference_anchor"] = reference["2"][0]
    with pytest.raises(ValueError, match="does not reproduce"):
        score(manifest, lock, gold, wrong_reference, pdf_dir)
    forged_mode = json.loads(json.dumps(predictions))
    forged_mode["papers"][0]["proposal_mode"] = "submitted_proposal"
    with pytest.raises(ValueError, match="independent pre-gold seal"):
        score(manifest, lock, gold, forged_mode, pdf_dir)
    forged_timing = json.loads(json.dumps(predictions))
    forged_timing["papers"][0]["elapsed_seconds"] = -100_000
    timing_report = score(manifest, lock, gold, forged_timing, pdf_dir)
    assert timing_report["measured_extraction_seconds"] >= 0
    assert timing_report["timing_basis"] == "independent_baseline_replay_during_scoring"
    costed = score(
        manifest,
        lock,
        gold,
        predictions,
        pdf_dir,
        costs={
            "schema": "citation-blind-eval.v1",
            "paper_count": 1,
            "compute_hour_usd": 0.12,
            "annotation_hours": 2,
            "annotation_hour_usd": 25,
        },
    )
    assert costed["annotation_cost_usd"] == 50
    assert costed["projected_total_cost_usd_per_100_papers"] > 5_000
    different_style_gold = json.loads(json.dumps(gold))
    different_style_gold["papers"][0]["occurrences"][0]["style"] = "other"
    different_style_lock = seal(manifest, different_style_gold, pdf_dir)
    different_style_predictions = run(manifest, different_style_lock, pdf_dir)
    different_style_report = score(
        manifest, different_style_lock, different_style_gold, different_style_predictions, pdf_dir
    )
    assert different_style_report["occurrence_precision"] == 1
    assert different_style_report["by_citation_style"]["numeric"]["precision"] == 0.5
    assert different_style_report["by_citation_style"]["other"]["recall"] == 0
    assert different_style_report["by_citation_style"]["other"]["gold_occurrence_count"] == 1
    assert different_style_report["by_citation_style"]["other"]["gold_paper_count"] == 1
    gold["papers"][0]["occurrences"][0]["target_identifier"] = "arxiv:0000.00000"
    with pytest.raises(ValueError, match="Unsealed or modified gold"):
        score(manifest, lock, gold, predictions, pdf_dir)


def test_blind_eval_counts_false_positives_and_unmappable_gold(tmp_path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = _pdf(["This [1] is a citation; [2] is not.", "References", "[1] Source."])
    (pdf_dir / "heldout.pdf").write_bytes(pdf)
    manifest = freeze(
        {
            "schema": "citation-blind-eval.v1",
            "not_used_for_development": True,
            "papers": [{"paper_id": "heldout", "source_uri": URI, "stratum": "numeric"}],
        },
        pdf_dir,
    )
    snapshot = CitationExtractionService._snapshot(pdf, URI)
    text = snapshot["pages"][0]["text"]
    start = text.index("[1]")
    gold = {
        "schema": "citation-blind-eval.v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "annotator_id": "human-annotator-1",
        "annotation_protocol": "visual-full-paper-v1",
        "papers": [
            {
                "paper_id": "heldout",
                "exhaustive": True,
                "occurrences": [
                    {
                        "page": 1,
                        "quote": "[1]",
                        "style": "numeric",
                        "anchor": {"page": 1, "start": start, "end": start + 3, "quote": "[1]"},
                        "reference_anchor": None,
                        "target_identifier": None,
                    },
                    {
                        "page": 1,
                        "quote": "unmappable visual callout",
                        "style": "other",
                        "anchor": None,
                        "reference_anchor": None,
                        "target_identifier": None,
                    },
                ],
            }
        ],
    }
    lock = seal(manifest, gold, pdf_dir)
    report = score(manifest, lock, gold, run(manifest, lock, pdf_dir), pdf_dir)
    assert report["counts"]["occurrence_tp"] == 1
    assert report["counts"]["occurrence_fp"] == 1
    assert report["counts"]["occurrence_fn"] == 1
    assert report["occurrence_precision"] == 0.5
    assert report["occurrence_recall"] == 0.5
    assert report["by_citation_style"]["numeric"]["precision"] == 0.5
    assert report["by_citation_style"]["other"]["recall"] == 0
    assert report["bibliographic_identity_precision"] is None


@pytest.mark.parametrize(
    "anchor",
    [
        {"page": 1, "start": -3, "end": -1, "quote": "de"},
        {"page": 1, "start": 1, "end": 99, "quote": "bcdef"},
        {"page": 1, "start": 0, "end": 0, "quote": ""},
    ],
)
def test_blind_gold_anchor_requires_exact_nonnegative_bounds(anchor: dict) -> None:
    with pytest.raises(ValueError, match="outside frozen"):
        _check_anchor(anchor, {1: "abcdef"})


def test_visual_quote_mapping_handles_layout_without_manual_offsets() -> None:
    text = "Body (Ac-\nme et al., 2025; Roe\net al., 2024) then another sentence."
    matches = find_text_matches(1, text, "(Acme et al., 2025; Roe et al., 2024)")
    assert len(matches) == 1
    anchor = matches[0]["anchor"]
    assert text[anchor["start"] : anchor["end"]] == anchor["quote"]
    assert anchor["quote"] == "(Ac-\nme et al., 2025; Roe\net al., 2024)"
    repeated = find_text_matches(2, "First [1], second [1].", "[1]")
    assert len(repeated) == 2
    assert repeated[0]["anchor"]["start"] != repeated[1]["anchor"]["start"]


def test_gold_draft_requires_human_page_review_and_specific_null_reason(tmp_path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "heldout.pdf").write_bytes(_pdf(["Study (Acme et al., 2025).", "References"]))
    manifest = freeze(
        {
            "schema": "citation-blind-eval.v1",
            "not_used_for_development": True,
            "papers": [{"paper_id": "heldout", "source_uri": URI, "stratum": "synthetic-source"}],
        },
        pdf_dir,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    draft_path = tmp_path / "gold.draft.json"
    draft = GoldDraft(manifest_path, pdf_dir, draft_path, "human-annotator-1")
    quote = "(Acme et al., 2025)"
    assert len(draft.matches("heldout", 1, quote)) == 1
    with pytest.raises(ValueError, match="Confirm why"):
        draft.add_occurrence("heldout", 1, quote, "author_year", None, "No mapping exists")
    added = draft.add_occurrence("heldout", 1, quote, "author_year", 0)
    assert added["anchor"]["quote"] == quote
    assert added["anchor"]["start"] >= 0
    assert draft.gold["papers"][0]["exhaustive"] is False
    draft.review_page("heldout", 1, True)
    assert draft.gold["papers"][0]["exhaustive"] is True
    assert seal(manifest, draft.gold, pdf_dir)["gold_sha256"]
    draft.delete_occurrence("heldout", added["annotation_id"])
    assert draft.gold["papers"][0]["exhaustive"] is False
    with pytest.raises(ValueError, match="specific human reason"):
        draft.add_occurrence("heldout", 1, "unseen visual callout", "other", None, "")
    missing = draft.add_occurrence(
        "heldout", 1, "unseen visual callout", "other", None, "visual marker is absent from text"
    )
    assert missing["anchor"] is None
    assert draft_path.exists()


def test_claim_relation_is_only_a_proposal(tmp_path) -> None:
    path, repository, service = _setup(tmp_path)
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study."])
    baseline = service.preview(CONTEXT, pdf, URI)
    occurrence = _input_occurrence(baseline["occurrences"][0])
    proposal = {
        "contract_version": "CitationExtractionProposal.v1",
        "occurrences": [occurrence],
        "claim_relation_proposals": [
            {
                "id": "r-1",
                "occurrence_id": occurrence["id"],
                "context_anchor": occurrence["anchor"],
                "target_claim_revision_id": "untrusted-revision-string",
                "relation_hint": "possibly_supports",
            }
        ],
    }
    preview = service.preview(CONTEXT, pdf, URI, json.dumps(proposal))
    assert preview["claim_relation_proposals"][0]["status"] == "unverified_semantics"
    result = service.commit(CONTEXT, pdf, URI, preview["preview_hash"], json.dumps(proposal))
    assert repository.get_artifact(CONTEXT.workspace_id, result["artifact_id"])
    assert _counts(path) == {key: 0 for key in _counts(path)}


def test_http_preview_and_commit(tmp_path) -> None:
    path, repository, service = _setup(tmp_path)
    app = FastAPI()
    app.include_router(
        create_citation_extraction_router(
            service=service,
            request_context_factory=lambda _request, tenant: RequestContext(
                "http-1", tenant.id, "admin-a"
            ),
            repository_provider=lambda: repository,
        )
    )
    app.dependency_overrides[current_admin_user] = lambda: {
        "id": "admin-a",
        "tenant_id": "workspace-a",
    }
    pdf = _pdf(["A claim [1].", "References", "[1] Doe. Study."])
    with TestClient(app) as client:
        preview = client.post(
            "/api/research/citation-extractions/preview",
            data={"source_uri": URI},
            files={"file": ("paper.pdf", pdf, "application/pdf")},
        )
        assert preview.status_code == 200
        committed = client.post(
            "/api/research/citation-extractions",
            data={"source_uri": URI, "expected_preview_hash": preview.json()["preview_hash"]},
            files={"file": ("paper.pdf", pdf, "application/pdf")},
        )
        assert committed.status_code == 201
        assert repository.get_artifact("workspace-a", committed.json()["artifact_id"])
    assert _counts(path) == {key: 0 for key in _counts(path)}
