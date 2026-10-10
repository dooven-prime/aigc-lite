# CitationExtractionProposal.v1 blind evaluation

This is an evaluation of **citation extraction**, not of whether one paper's argument supports another. A citation occurrence, a linked bibliography entry, and a verified external work identity are separate outcomes. None can establish a claim-premise or under-which-conditions relation by itself.

## Blindness and annotation contract

1. Select full PDFs **not used to develop this extractor**, including numeric, author–year, and difficult two-column layouts. Do not inspect extractor outputs for them. The three PDFs in the existing smoke test are forbidden; the freeze command also rejects their known digests. Keep the PDFs and annotation files outside the repository, for example under ignored `tmp/pdfs/blind/` and `tmp/citation-blind/`.
2. Fill a cohort spec with `schema`, `not_used_for_development: true`, and `papers` containing `paper_id`, HTTPS `source_uri`, and predeclared **source** `stratum` (for example `acl-2026` or `arxiv-math-2026`). Put each PDF at `<pdf-dir>/<paper_id>.pdf`. `freeze` records SHA-256 for every PDF, the extractor/contract/evaluator source hashes, and the `pypdf` version. A content hash binds **these bytes**, not the authenticity of the upstream URL or Git tree.
3. A human annotator reads **each complete visual PDF**, without seeing predictions, and records every in-body citation callout, including those missing from PDF text extraction. Gold has one entry per callout with `page`, human-labeled `style` (`numeric`, `author_year`, or `other`), optional exact extracted-text `anchor`, optional exact `reference_anchor`, and optional verified external `target_identifier`. A callout that genuinely cannot be mapped into extracted text keeps `anchor: null` and a specific mapping reason; it counts as a missed occurrence. Do not use null merely to save work. `exhaustive: true` means the whole paper was checked, not just sampled pages or regex hits. If there are no citations, use an empty list. Annotators must resolve ambiguity from the PDF and record a reason in their separate notes; a second annotator and adjudication are recommended for a publishable result.
4. `seal` checks cohort coverage, PDF hashes, and `0 <= start < end <= page_length` for every supplied text anchor, then records the gold JSON digest in a lock file. The `run` command sees only the manifest and **lock**, not the gold JSON. It refuses extractor or PDF drift. `score` refuses a changed gold digest, independently replays the deterministic baseline from the frozen PDFs, and compares every deterministic prediction field before computing metrics. Submitted timestamps and elapsed time are not treated as prediction provenance. Keep manifest, gold, lock, predictions, and report as immutable evaluation records; none are QualificationReceipts or CurrentUseBindings.

Minimal cohort spec:

```json
{"schema":"citation-blind-eval.v1","not_used_for_development":true,"papers":[{"paper_id":"paper-001","source_uri":"https://publisher.example/paper.pdf","stratum":"acl-2026"}]}
```

Gold skeleton (replace with **human** labels; these are field examples, not ground truth):

```json
{
  "schema": "citation-blind-eval.v1",
  "manifest_sha256": "<digest from manifest>",
  "annotator_id": "<human annotator>",
  "annotation_protocol": "visual-full-paper-v1",
  "papers": [{
    "paper_id": "paper-001", "exhaustive": true,
    "occurrences": [{
      "page": 2, "quote": "[1]", "style": "numeric",
      "anchor": {"page": 2, "start": 120, "end": 123, "quote": "[1]"},
      "reference_anchor": {"page": 8, "start": 19, "end": 95, "quote": "[1] ..."},
      "target_identifier": "10.0000/example"
    }]
  }]
}
```

`start/end` above are illustrative. Both anchors must match the frozen `pypdf`-extracted Unicode page text exactly. `reference_anchor` means the actual bibliography entry, not an unrelated DOI-bearing line. Use `null` when the PDF does not provide a defensible mapping or identifier. A DOI/arXiv ID copied from a predicted result is **not** independent human ground truth.

### No-offset local annotation helper

The local helper removes manual JSON and character counting. It serves only the frozen PDF and `pypdf` text on loopback; it never runs citation detection/prediction or shows predictions. The annotator reviews the PDF page, selects or pastes a visible citation, chooses its style, and confirms one proposed source-text span using the displayed context. Whitespace and line-end hyphen normalization are **search aids only**: the saved `anchor.quote`, `start`, and `end` come from the exact frozen text. A separate `visual_quote` preserves what the annotator supplied. The current v1 seal contract requires `quote == anchor.quote`, so for mapped records the helper writes the exact extracted quote into `quote` rather than making a visual transcription masquerade as an exact text span. The draft also records hashes of the helper and UI source for annotation-process provenance.

```powershell
python -m scripts.citation_gold_annotator --manifest tmp/citation-blind/v3/manifest.json --pdf-dir tmp/pdfs/blind-v1 --draft tmp/citation-blind/v3/gold.draft.json --annotator-id <your-id> --port 8014
```

Open `http://127.0.0.1:8014/`. The helper binds only `127.0.0.1` and requires a per-process token for writes. After checking **every page**, explicitly mark it reviewed, including pages with zero citations. Adding or deleting a record clears that page's reviewed flag. A paper becomes `exhaustive: true` only when every page is marked; the current `seal` still requires **every paper in the frozen six-paper cohort** to be exhaustive. This is a procedural checklist, not cryptographic proof of human completeness. The draft is local and ignored by Git; keep an independent backup if valuable. The helper records occurrences only: `reference_anchor` and `target_identifier` remain null until a separate human bibliography-identity pass, so those identity metrics cannot be claimed from this draft alone.

If a quote yields no match, first try selecting its exact text from the extracted-text pane. `anchor: null` requires a specific explanation. If the helper presents candidates but all are visibly wrong, the explanation must begin with `rejected_candidates:`. Never choose a candidate solely because it appears first. The human, not the matching normalization, decides whether the span corresponds to the PDF callout.

If six complete papers are too expensive, keep v3 as **unscored** and preregister a separate sampled-page protocol and manifest before looking at predictions. That protocol must define sampled pages and its recall denominator explicitly; partially annotated pages cannot be presented as full-paper Gold and cannot silently replace v3's contract.

```powershell
python scripts/citation_blind_eval.py freeze --spec tmp/citation-blind/cohort.json --pdf-dir tmp/pdfs/blind --out tmp/citation-blind/manifest.json
python scripts/citation_blind_eval.py seal --manifest tmp/citation-blind/manifest.json --gold tmp/citation-blind/gold.json --pdf-dir tmp/pdfs/blind --out tmp/citation-blind/gold.lock.json
python scripts/citation_blind_eval.py run --manifest tmp/citation-blind/manifest.json --lock tmp/citation-blind/gold.lock.json --pdf-dir tmp/pdfs/blind --out tmp/citation-blind/predictions.json
python scripts/citation_blind_eval.py score --manifest tmp/citation-blind/manifest.json --lock tmp/citation-blind/gold.lock.json --gold tmp/citation-blind/gold.json --predictions tmp/citation-blind/predictions.json --pdf-dir tmp/pdfs/blind --out tmp/citation-blind/report.json
```

For an economic per-100 projection, record actual labor hours and the chosen compute/labor rates in a separate local `costs.json`, then add `--costs tmp/citation-blind/costs.json` to `score`. Its schema is `{"schema":"citation-blind-eval.v1","paper_count":6,"compute_hour_usd":0.12,"annotation_hours":18,"annotation_hour_usd":25}` (numbers here are **illustrative**, not measured). Without such a ledger, the total dollar cost remains `null` rather than an invented zero. The measured extraction time is from the scoring replay; the initial run's self-reported elapsed time is not used for cost projection.

No command overwrites an existing frozen file. Hashes detect accidental changes but are not signatures or proof that the annotator remained blind; an independent annotator and restricted access to gold are procedural requirements. The v1 scorer **rejects model-generated predictions**: future model assessment must seal a prediction digest with an independent controller *before* Gold is opened. For a new extractor revision, start a **new cohort**; do not tune on the sealed holdout and then report that same cohort as blind. All fields containing source quotes may contain copyrighted text; respect the source license when sharing benchmark records.

## Scores and limitations

- Occurrence precision/recall use exact page/text spans for `body_anchored` predictions. Visually present callouts with no extractable-text anchor remain in the recall denominator. Accepted ordinary text is a false positive. `abstained` predictions are reported separately; they do not count as true positives.
- `by_citation_style` separately reports precision, recall, abstention, and false-confirmation rates using the human-labeled Gold style; `by_source_stratum` uses the predeclared source grouping. These are independent axes: a source stratum is **not** a citation-style label. Each style reports `gold_occurrence_count` and `gold_paper_count`; each source stratum reports `gold_occurrence_count` and `paper_count`. A wrong predicted style is a false positive for the predicted style and a false negative for the Gold style. Style labels must be assigned without seeing predictions; the schema cannot by itself prove that blinding procedure was followed. Interpret all strata with their denominators; no pooled "accuracy" substitutes for them.
- Reference-entry precision measures whether an accepted match points to the human-annotated bibliography entry. Accepted matches whose gold bibliography entry cannot be mapped are counted as `reference_unscorable`, not silently treated as correct. A `reference_anchored` status by itself is **not** a verified external work identity.
- Bibliographic identity precision measures accepted arXiv/DOI identifiers against human-verified identifiers. Predictions where the human gold has no resolvable identifier are `identity_unscorable`, not automatic true/false positives. Identity recall uses all gold occurrences with a known identifier. The identity abstention rate is reported separately among detected gold occurrences with a known identifier. Where no scorable identifier predictions exist, precision is `null`, not 100%.
- The report includes `truncated_papers`; if the 200-occurrence cap is reached, missing callouts remain false negatives. This must be stated when interpreting recall.
- The scorer measures independent baseline replay time and extrapolates seconds per 100 papers; this is **not** a 100-paper benchmark. Deterministic baseline model spend is $0. With a measured local cost ledger, the scorer projects total dollars per 100 papers; otherwise that total is `null`. PDF acquisition, storage, initial-run compute, and operator overhead remain excluded.

The prior 74/11/42 occurrence counts are smoke-test observations, not blind precision or recall. The new evaluation remains **unscored until a genuinely independent human gold set is sealed**. One possible externally curated source is the [CEX GoldStandard Fulltext](https://zenodo.org/records/16310561): it describes 107 articles across 27 disciplines and manually corrected citation files, but not every PDF is openly available, and its annotation format is not the exact extracted-text span format above. An independent, audited conversion would be needed before it could be scored here. Publisher XML or raw GROBID output cannot silently replace visual-PDF human annotations; [GROBID's own end-to-end documentation](https://grobid.readthedocs.io/en/latest/End-to-end-evaluation/) describes XML/PDF alignment and annotation gaps.

Original PDF bytes are not embedded in the application's CitationExtractionProposal Artifact. The local blind corpus retains them for this evaluation, but long-term portable verification needs a separate content-addressed, rights-aware original store. Hashes alone cannot reconstruct missing PDFs.

Only after this extraction layer has measured error rates should a later `ClaimRelationProposal` evaluation ask whether a cited argument depends on a specified premise, under a specific scope, with a separately checked semantic relation. Citation linking alone cannot answer that question.

## Graph boundary

- **L1 — Citation graph:** an in-body occurrence, bibliography-entry match, and external work identity are separate claims. A yearless author mention such as “Tao et al.” or “Wei et al.” is not automatically an identifiable author–year citation. One span listing several organizations is not automatically several works or independent evidence lines. The v1 single-number matcher cannot express a one-to-many citation group; ambiguous cases must remain unmatched/abstained and be counted in Gold, not manufactured into links.
- **L2 — Claim-citation graph:** connecting a precise claim revision in the citing work to a precise claim revision in the cited work requires separate source spans, revision identity, and semantic review. L1 does not imply `supports`.
- **L3 — Argument-premise graph:** premises, scope, assumptions, and inference edges need a domain-specific checked relation. Neither reference identity nor model agreement establishes entailment. No L1/L2 output may directly issue a QualificationReceipt or CurrentUseBinding.

The yearless-mention and grouped-name cases are evaluation categories, **not** reasons to tune this extractor on the frozen holdout. Any parser change requires a new development set and a new sealed evaluation cohort.
