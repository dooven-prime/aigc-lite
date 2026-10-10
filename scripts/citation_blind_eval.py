"""Sealed, candidate-only citation extraction evaluation.

The runner accepts a gold *lock*, not the annotations. It refuses source or
extractor drift; the scorer refuses unsealed annotations. Neither writes to
the application repository or changes research authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pypdf

from app.core.contracts import RequestContext
from app.services.citation_extraction import MAX_PDF_BYTES, CitationExtractionService

SCHEMA = "citation-blind-eval.v1"
CITATION_STYLES = ("numeric", "author_year", "other")
DEVELOPMENT_PDF_DIGESTS = {
    "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
    "d8bab2c19af822c5477f545ce4a1121c39d249b04b56c8d547932b2fc82b12d8",
    "67eb7c07dee6dcd95d0a2745eb9a7b781a68f4c01f0594426912045d171589a1",
}
EXTRACTOR_SOURCE = Path(__file__).resolve().parents[1] / "app/services/citation_extraction.py"
CONTRACT_SOURCE = Path(__file__).resolve().parents[1] / "app/core/citation_extraction.py"
EVALUATOR_SOURCE = Path(__file__).resolve()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _write(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite frozen evaluation file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_manifest(manifest: dict) -> None:
    expected = manifest.get("manifest_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("schema") != SCHEMA or expected != _digest(_canonical(unsigned)):
        raise ValueError("Manifest schema or digest mismatch")
    if not manifest.get("papers"):
        raise ValueError("Blind cohort is empty")
    ids = [paper["paper_id"] for paper in manifest["papers"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate paper ID")


def _check_code(manifest: dict) -> None:
    for field, path in (
        ("extractor_source_sha256", EXTRACTOR_SOURCE),
        ("contract_source_sha256", CONTRACT_SOURCE),
        ("evaluator_source_sha256", EVALUATOR_SOURCE),
    ):
        if _digest(path.read_bytes()) != manifest[field]:
            raise ValueError(f"{path.name} changed after cohort freeze")
    if pypdf.__version__ != manifest["pypdf_version"]:
        raise ValueError("pypdf version changed after cohort freeze")


def freeze(spec: dict, pdf_dir: Path) -> dict:
    if spec.get("schema") != SCHEMA or spec.get("not_used_for_development") is not True:
        raise ValueError("Explicit fresh-cohort declaration is required")
    papers = []
    for paper in spec.get("papers", []):
        paper_id = paper["paper_id"]
        if (
            not paper_id
            or Path(paper_id).name != paper_id
            or not paper_id.replace("-", "").replace("_", "").isalnum()
        ):
            raise ValueError(f"Unsafe paper ID: {paper_id!r}")
        source_uri = paper["source_uri"]
        if urlsplit(source_uri).scheme != "https":
            raise ValueError(f"HTTPS source required for {paper_id}")
        content = (pdf_dir / f"{paper_id}.pdf").read_bytes()
        if not content.startswith(b"%PDF-") or not 0 < len(content) <= MAX_PDF_BYTES:
            raise ValueError(f"Unsupported PDF for {paper_id}")
        digest = _digest(content)
        if digest in DEVELOPMENT_PDF_DIGESTS:
            raise ValueError(f"{paper_id}: known development PDF cannot enter blind cohort")
        papers.append(
            {
                "paper_id": paper_id,
                "source_uri": source_uri,
                "stratum": paper["stratum"],
                "pdf_sha256": digest,
                "pdf_size_bytes": len(content),
            }
        )
    manifest = {
        "schema": SCHEMA,
        "created_at": _now(),
        "not_used_for_development": True,
        "extractor_source_sha256": _digest(EXTRACTOR_SOURCE.read_bytes()),
        "contract_source_sha256": _digest(CONTRACT_SOURCE.read_bytes()),
        "evaluator_source_sha256": _digest(EVALUATOR_SOURCE.read_bytes()),
        "pypdf_version": pypdf.__version__,
        "papers": papers,
    }
    manifest["manifest_sha256"] = _digest(_canonical(manifest))
    _check_manifest(manifest)
    return manifest


def _anchor_key(anchor: dict | None) -> tuple | None:
    if anchor is None:
        return None
    return (anchor["page"], anchor["start"], anchor["end"], anchor["quote"])


def _check_anchor(anchor: dict | None, pages: dict[int, str]) -> None:
    if anchor is None:
        return
    page = pages.get(anchor["page"])
    start, end = anchor["start"], anchor["end"]
    if (
        page is None
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= len(page)
        or page[start:end] != anchor["quote"]
    ):
        raise ValueError("Gold anchor is outside frozen extracted PDF text or does not match it")


def seal(manifest: dict, gold: dict, pdf_dir: Path) -> dict:
    _check_manifest(manifest)
    _check_code(manifest)
    if gold.get("schema") != SCHEMA or gold.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("Gold does not bind the frozen cohort")
    if not gold.get("annotator_id") or not gold.get("annotation_protocol"):
        raise ValueError("Human annotator identity and protocol are required")
    gold_papers = {paper["paper_id"]: paper for paper in gold.get("papers", [])}
    if set(gold_papers) != {paper["paper_id"] for paper in manifest["papers"]}:
        raise ValueError("Gold must cover every frozen paper exactly once")
    if len(gold_papers) != len(gold.get("papers", [])):
        raise ValueError("Duplicate paper in gold")
    for paper in manifest["papers"]:
        item = gold_papers[paper["paper_id"]]
        if item.get("exhaustive") is not True:
            raise ValueError(f"{paper['paper_id']}: non-exhaustive gold cannot be sealed")
        content = (pdf_dir / f"{paper['paper_id']}.pdf").read_bytes()
        if _digest(content) != paper["pdf_sha256"]:
            raise ValueError(f"{paper['paper_id']}: PDF changed")
        snapshot = CitationExtractionService._snapshot(content, paper["source_uri"])
        pages = {page["page"]: page["text"] for page in snapshot["pages"]}
        seen: set[tuple] = set()
        for occurrence in item.get("occurrences", []):
            if occurrence.get("page") not in pages or not occurrence.get("quote"):
                raise ValueError("Each visual citation needs a page and quote")
            if occurrence.get("style") not in CITATION_STYLES:
                raise ValueError("Each gold citation requires a human-labeled style")
            if occurrence.get("anchor") is not None and (
                occurrence["anchor"]["page"] != occurrence["page"]
                or occurrence["anchor"]["quote"] != occurrence["quote"]
            ):
                raise ValueError("Visual marker and extracted-text anchor disagree")
            _check_anchor(occurrence.get("anchor"), pages)
            _check_anchor(occurrence.get("reference_anchor"), pages)
            key = _anchor_key(occurrence.get("anchor"))
            if key is not None and key in seen:
                raise ValueError("Duplicate gold occurrence anchor")
            if key is not None:
                seen.add(key)
    return {
        "schema": SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "gold_sha256": _digest(_canonical(gold)),
        "sealed_at": _now(),
        "annotator_id": gold["annotator_id"],
    }


def run(manifest: dict, lock: dict, pdf_dir: Path) -> dict:
    _check_manifest(manifest)
    if lock.get("manifest_sha256") != manifest["manifest_sha256"] or not lock.get("gold_sha256"):
        raise ValueError("A matching sealed gold lock is required before extraction")
    _check_code(manifest)
    service = CitationExtractionService()
    predictions = []
    for paper in manifest["papers"]:
        content = (pdf_dir / f"{paper['paper_id']}.pdf").read_bytes()
        if _digest(content) != paper["pdf_sha256"]:
            raise ValueError(f"{paper['paper_id']}: PDF changed")
        started = time.perf_counter()
        preview = service.preview(
            RequestContext("blind-eval", "blind-eval", "blind-eval-runner"),
            content,
            paper["source_uri"],
        )
        elapsed = time.perf_counter() - started
        predictions.append(
            {
                "paper_id": paper["paper_id"],
                "pdf_sha256": preview["pdf_sha256"],
                "elapsed_seconds": elapsed,
                "occurrences": preview["occurrences"],
                "bibliographic_matches": preview["bibliographic_matches"],
                "truncated": preview["truncated"],
                "proposal_mode": preview["proposal_mode"],
            }
        )
    return {
        "schema": SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "gold_sha256": lock["gold_sha256"],
        "extractor_source_sha256": manifest["extractor_source_sha256"],
        "run_at": _now(),
        "papers": predictions,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _occurrence_rates(counts: dict, *, gold_paper_count: int | None = None) -> dict:
    tp = counts["tp"]
    fp = counts["fp"]
    fn = counts["fn"]
    return {
        **counts,
        "gold_occurrence_count": tp + fn,
        "gold_paper_count": gold_paper_count,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "abstention_rate": _ratio(counts["abstained"], counts["proposed"]),
        "false_confirmation_rate": _ratio(fp, tp + fp),
    }


def _empty_occurrence_counts() -> dict:
    return {"tp": 0, "fp": 0, "fn": 0, "abstained": 0, "proposed": 0}


def _replay_predictions(manifest: dict, lock: dict, predictions: dict, pdf_dir: Path) -> float:
    if any(
        item.get("proposal_mode") != "deterministic_baseline"
        for item in predictions["papers"]
    ):
        raise ValueError("Model predictions require an independent pre-gold seal")
    replay = run(manifest, lock, pdf_dir)
    replay_papers = {paper["paper_id"]: paper for paper in replay["papers"]}
    reproducible_fields = (
        "pdf_sha256",
        "occurrences",
        "bibliographic_matches",
        "truncated",
        "proposal_mode",
    )
    for paper in predictions["papers"]:
        actual = replay_papers[paper["paper_id"]]
        if any(paper.get(field) != actual[field] for field in reproducible_fields):
            raise ValueError("Submitted prediction does not reproduce the frozen baseline")
    return sum(paper["elapsed_seconds"] for paper in replay["papers"])


def score(
    manifest: dict,
    lock: dict,
    gold: dict,
    predictions: dict,
    pdf_dir: Path,
    costs: dict | None = None,
) -> dict:
    _check_manifest(manifest)
    _check_code(manifest)
    digest = manifest["manifest_sha256"]
    if any(item.get("manifest_sha256") != digest for item in (lock, gold, predictions)):
        raise ValueError("Manifest binding mismatch")
    if (
        lock.get("gold_sha256") != _digest(_canonical(gold))
        or predictions.get("gold_sha256") != lock["gold_sha256"]
    ):
        raise ValueError("Unsealed or modified gold")
    if predictions.get("extractor_source_sha256") != manifest["extractor_source_sha256"]:
        raise ValueError("Prediction extractor does not match frozen cohort")
    gold_papers = {paper["paper_id"]: paper for paper in gold["papers"]}
    predicted_papers = {paper["paper_id"]: paper for paper in predictions["papers"]}
    paper_ids = {paper["paper_id"] for paper in manifest["papers"]}
    if set(gold_papers) != paper_ids or set(predicted_papers) != paper_ids:
        raise ValueError("Evaluation must cover exactly the frozen cohort")
    if len(gold_papers) != len(gold["papers"]) or len(predicted_papers) != len(predictions["papers"]):
        raise ValueError("Duplicate paper in evaluation")
    seconds = _replay_predictions(manifest, lock, predictions, pdf_dir)
    style_counts = {style: _empty_occurrence_counts() for style in CITATION_STYLES}
    style_gold_papers = {style: set() for style in CITATION_STYLES}
    source_counts: dict[str, dict] = {}
    source_papers: dict[str, set] = {}
    metrics = {
        key: 0
        for key in (
            "occurrence_tp",
            "occurrence_fp",
            "occurrence_fn",
            "occurrence_abstained",
            "occurrence_proposed",
            "reference_tp",
            "reference_fp",
            "reference_unscorable",
            "reference_eligible",
            "identity_tp",
            "identity_fp",
            "identity_unscorable",
            "identity_fn",
            "identity_eligible",
            "identity_abstained_on_detected",
            "detected_identity_eligible",
            "truncated_papers",
        )
    }
    for paper in manifest["papers"]:
        paper_id = paper["paper_id"]
        gold_item = gold_papers[paper_id]
        predicted = predicted_papers[paper_id]
        if (
            gold_item.get("exhaustive") is not True
            or predicted["pdf_sha256"] != paper["pdf_sha256"]
        ):
            raise ValueError(f"{paper_id}: incomplete gold or wrong PDF")
        source = paper["stratum"]
        source_metric = source_counts.setdefault(source, _empty_occurrence_counts())
        source_papers.setdefault(source, set()).add(paper_id)
        metrics["truncated_papers"] += predicted.get("truncated") is True
        gold_by_anchor = {
            _anchor_key(item.get("anchor")): item
            for item in gold_item["occurrences"]
            if item.get("anchor") is not None
        }
        accepted = {
            item["id"]: item
            for item in predicted["occurrences"]
            if item.get("status") == "body_anchored"
        }
        accepted_keys = {_anchor_key(item["anchor"]) for item in accepted.values()}
        accepted_style_keys = {
            (_anchor_key(item["anchor"]), item["style"]) for item in accepted.values()
        }
        gold_style_keys = {
            (_anchor_key(item.get("anchor")), item["style"])
            for item in gold_item["occurrences"]
            if item.get("anchor") is not None
        }
        for item in gold_item["occurrences"]:
            style = item["style"]
            style_gold_papers[style].add(paper_id)
            if (_anchor_key(item.get("anchor")), style) not in accepted_style_keys:
                style_counts[style]["fn"] += 1
        for item in predicted["occurrences"]:
            style = item["style"]
            if style not in style_counts:
                raise ValueError("Unknown predicted citation style")
            style_counts[style]["proposed"] += 1
            source_metric["proposed"] += 1
            if item.get("status") != "body_anchored":
                style_counts[style]["abstained"] += 1
                source_metric["abstained"] += 1
            elif (_anchor_key(item["anchor"]), style) in gold_style_keys:
                style_counts[style]["tp"] += 1
            else:
                style_counts[style]["fp"] += 1
        metrics["occurrence_proposed"] += len(predicted["occurrences"])
        metrics["occurrence_abstained"] += sum(
            item.get("status") != "body_anchored" for item in predicted["occurrences"]
        )
        tp_keys = accepted_keys & gold_by_anchor.keys()
        metrics["occurrence_tp"] += len(tp_keys)
        metrics["occurrence_fp"] += len(accepted_keys - gold_by_anchor.keys())
        metrics["occurrence_fn"] += len(gold_item["occurrences"]) - len(tp_keys)
        source_metric["tp"] += len(tp_keys)
        source_metric["fp"] += len(accepted_keys - gold_by_anchor.keys())
        source_metric["fn"] += len(gold_item["occurrences"]) - len(tp_keys)
        matching_gold = {
            item["id"]: gold_by_anchor[_anchor_key(item["anchor"])]
            for item in accepted.values()
            if _anchor_key(item["anchor"]) in gold_by_anchor
        }
        identifier_matches: dict[str, tuple[str, bool]] = {}
        for match in predicted["bibliographic_matches"]:
            if match.get("status") != "reference_anchored":
                continue
            annotation = matching_gold.get(match["occurrence_id"])
            if annotation is None:
                metrics["reference_fp"] += 1
                if match.get("target_identifier"):
                    metrics["identity_fp"] += 1
                continue
            reference = annotation.get("reference_anchor")
            if reference is None:
                metrics["reference_unscorable"] += 1
            else:
                metrics[
                    "reference_tp"
                    if _anchor_key(reference) == _anchor_key(match["reference_anchor"])
                    else "reference_fp"
                ] += 1
            if match.get("target_identifier"):
                actual = annotation.get("target_identifier")
                if actual is None:
                    metrics["identity_unscorable"] += 1
                    continue
                correct_reference = reference is None or _anchor_key(reference) == _anchor_key(
                    match["reference_anchor"]
                )
                identifier_matches[match["occurrence_id"]] = (
                    match["target_identifier"].casefold(),
                    correct_reference,
                )
                correct_identity = (
                    correct_reference
                    and actual
                    and actual.casefold() == match["target_identifier"].casefold()
                )
                metrics["identity_tp" if correct_identity else "identity_fp"] += 1
        metrics["reference_eligible"] += sum(
            item.get("reference_anchor") is not None for item in gold_item["occurrences"]
        )
        known_identity = [
            item for item in gold_item["occurrences"] if item.get("target_identifier")
        ]
        metrics["identity_eligible"] += len(known_identity)
        metrics["identity_fn"] += sum(
            not any(
                _anchor_key(pred["anchor"]) == _anchor_key(item.get("anchor"))
                and identifier_matches.get(pred["id"])
                == (item["target_identifier"].casefold(), True)
                for pred in accepted.values()
            )
            for item in known_identity
        )
        for pred_id, item in matching_gold.items():
            if item.get("target_identifier"):
                metrics["detected_identity_eligible"] += 1
                metrics["identity_abstained_on_detected"] += pred_id not in identifier_matches
    occurrence_precision = _ratio(
        metrics["occurrence_tp"], metrics["occurrence_tp"] + metrics["occurrence_fp"]
    )
    occurrence_recall = _ratio(
        metrics["occurrence_tp"], metrics["occurrence_tp"] + metrics["occurrence_fn"]
    )
    baseline_only = all(
        item.get("proposal_mode") == "deterministic_baseline" for item in predicted_papers.values()
    )
    model_spend = 0.0 if baseline_only else None
    annotation_cost = None
    compute_cost = None
    total_cost_per_100 = None
    if costs is not None:
        if costs.get("schema") != SCHEMA or costs.get("paper_count") != len(paper_ids):
            raise ValueError("Cost ledger must bind this cohort size")
        fields = ("compute_hour_usd", "annotation_hours", "annotation_hour_usd")
        if any(not isinstance(costs.get(key), (int, float)) or costs[key] < 0 for key in fields):
            raise ValueError("Measured nonnegative cost rates and hours are required")
        if not baseline_only:
            if (
                not isinstance(costs.get("model_spend_usd"), (int, float))
                or costs["model_spend_usd"] < 0
            ):
                raise ValueError("Model-assisted run requires measured model spend")
            model_spend = costs["model_spend_usd"]
        compute_cost = seconds / 3600 * costs["compute_hour_usd"]
        annotation_cost = costs["annotation_hours"] * costs["annotation_hour_usd"]
        total_cost_per_100 = (compute_cost + annotation_cost + model_spend) * 100 / len(paper_ids)
    return {
        "schema": SCHEMA,
        "manifest_sha256": digest,
        "gold_sha256": lock["gold_sha256"],
        "paper_count": len(paper_ids),
        "counts": metrics,
        "by_citation_style": {
            style: _occurrence_rates(
                counts, gold_paper_count=len(style_gold_papers[style])
            )
            for style, counts in style_counts.items()
        },
        "by_source_stratum": {
            source: {
                **_occurrence_rates(counts),
                "paper_count": len(source_papers[source]),
            }
            for source, counts in source_counts.items()
        },
        "occurrence_precision": occurrence_precision,
        "occurrence_recall": occurrence_recall,
        "reference_entry_precision": _ratio(
            metrics["reference_tp"], metrics["reference_tp"] + metrics["reference_fp"]
        ),
        "bibliographic_identity_precision": _ratio(
            metrics["identity_tp"], metrics["identity_tp"] + metrics["identity_fp"]
        ),
        "bibliographic_identity_recall": _ratio(
            metrics["identity_tp"], metrics["identity_eligible"]
        ),
        "occurrence_abstention_rate": _ratio(
            metrics["occurrence_abstained"], metrics["occurrence_proposed"]
        ),
        "identity_abstention_rate_on_detected_gold": _ratio(
            metrics["identity_abstained_on_detected"], metrics["detected_identity_eligible"]
        ),
        "measured_extraction_seconds": seconds,
        "timing_basis": "independent_baseline_replay_during_scoring",
        "projected_extraction_seconds_per_100_papers": seconds * 100 / len(paper_ids),
        "model_spend_usd": model_spend,
        "compute_cost_usd": compute_cost,
        "annotation_cost_usd": annotation_cost,
        "projected_total_cost_usd_per_100_papers": total_cost_per_100,
        "limitations": [
            "The 100-paper time is an extrapolation, not a measured 100-paper run.",
            "Download, storage, and operator time are not costed; optional rates are self-reported.",
            "External identifier precision is not inferred from reference-entry anchoring.",
            "No claim-premise, support, entailment, or qualification relation is evaluated.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_parser = commands.add_parser("freeze")
    freeze_parser.add_argument("--spec", type=Path, required=True)
    freeze_parser.add_argument("--pdf-dir", type=Path, required=True)
    freeze_parser.add_argument("--out", type=Path, required=True)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("--manifest", type=Path, required=True)
    seal_parser.add_argument("--gold", type=Path, required=True)
    seal_parser.add_argument("--pdf-dir", type=Path, required=True)
    seal_parser.add_argument("--out", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--lock", type=Path, required=True)
    run_parser.add_argument("--pdf-dir", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    score_parser = commands.add_parser("score")
    for name in ("manifest", "lock", "gold", "predictions", "out"):
        score_parser.add_argument(f"--{name}", type=Path, required=True)
    score_parser.add_argument("--costs", type=Path)
    score_parser.add_argument("--pdf-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "freeze":
            result = freeze(_read(arguments.spec), arguments.pdf_dir)
        elif arguments.command == "seal":
            result = seal(_read(arguments.manifest), _read(arguments.gold), arguments.pdf_dir)
        elif arguments.command == "run":
            result = run(_read(arguments.manifest), _read(arguments.lock), arguments.pdf_dir)
        else:
            result = score(
                _read(arguments.manifest),
                _read(arguments.lock),
                _read(arguments.gold),
                _read(arguments.predictions),
                arguments.pdf_dir,
                _read(arguments.costs) if arguments.costs else None,
            )
        _write(arguments.out, result)
        print(f"{arguments.command}: {arguments.out}")
    except (ValueError, FileNotFoundError, FileExistsError, KeyError) as exc:
        print(f"blind evaluation refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
