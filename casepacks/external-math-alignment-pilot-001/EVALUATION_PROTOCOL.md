# External Mathematical Claim Alignment Audit — pilot evaluation protocol

Status: **proposed future review-withheld comparison**, not a completed evaluation. The pinned Lemma 8.6 / (8.19) and pressure-flux (10.19) pairs are already published allegations; the trace-square example is in the review paper itself. All three are known-answer development items and contribute no blind score.

## Question, tasks, and units

The measured unit is one precisely located manuscript statement and one proposed formal declaration at a pinned source identity. It is not a paper, repo, theorem family, or overall Navier–Stokes result. For each unit, an adjudicator records separately: objects/definitions, domain and quantifiers, hypotheses, conclusion, statement alignment, proof-method alignment, and whether an actual kernel run occurred. A mere name or citation match is not an alignment decision.

There are three **separate questions**, not one contest in which all methods must solve the same problem:

| Question | Evaluation | Boundary |
| --- | --- | --- |
| Which declaration corresponds to a manuscript statement? | A separately pre-registered *location task*, if attempted | Manual pre-location in the alignment task is disclosed, not credited to either semantic method |
| Are the *pre-located* statements aligned under the checked definitions, quantifiers, hypotheses, and conclusions? | Ordinary LLM review versus source-bound audit | Different proof methods alone are not a statement gap |
| Did the exact formal declaration pass the pinned kernel replay? | Kernel replay only | Kernel acceptance says nothing by itself about manuscript identity |

An integrated audit report may combine these observations and human adjudication, but its credibility is measured by accurate source binding, separated verdicts, disclosed uncertainty, and reproducibility—not by treating kernel-only as a semantic detector. Kernel-only missing a manuscript/formal mismatch is **not** a semantic false negative.

## Source freeze and information barrier

1. An independent case curator pre-registers a small, varied collection of further *located pairs*: checked-scope alignments, stronger formal assumptions, weaker formal conclusions, and a same-statement/different-method control. Cases with no corresponding declaration belong to the separate location task; they cannot masquerade as pre-located semantic pairs. The curator freezes exact source bytes and revision IDs, the proposed pair identity, page/line locations, source windows (including needed definitions and assumptions), reference labels, and adjudication rubric **before** either semantic method sees a case. Store the reference answers outside the input bundles and seal their digest. Do not revise the cohort, labels, windows, or rubric after seeing predictions; corrections require a new evaluation revision.
2. For the semantic comparison, give ordinary LLM review and source-bound audit the **same pre-located statement pair and the same frozen manuscript/Lean windows**. Provide identical bounded access to any additional frozen context; record each request. Neither method gets the independent review, previous audit notes, hidden labels, or unrestricted search. The paired-input design measures comparison, not declaration discovery. If declaration location is evaluated later, score it in the separate location task and disclose any human pre-location effort rather than silently granting it to one method.
3. Freeze the LLM prompt/model/settings and the source-bound detector code/configuration before opening withheld cases. Apply the same predeclared wall-time and source-access limits to both semantic methods; record any unequal model budget or human assistance. A detector specialized to Lemma 8.6 may abstain on new syntax. Tuning it on withheld answers turns those cases into development data and requires a fresh withheld cohort.
4. Seal each prediction file and run metadata (SHA-256, exact input manifest, model and detector versions, prompts, tools, wall time, usage status, operator identity, and any assistance) **before** revealing reference answers. An adjudicator then checks every claimed gap and alignment against exact source positions, records unresolved disagreements, and discloses whether adjudicators were blind to method identity. A model's self-reported source location is not an independent source check.
5. Publicly described pairs cannot be guaranteed absent from model training. Call this **review-withheld**, not truly unseen. Report known public exposure, possible answer leakage, and exclusions per case rather than claiming independent discovery.

## Semantic methods and separate kernel track

| Method or track | Input and allowed activity | Claim it can make |
| --- | --- | --- |
| Ordinary LLM semantic review | Pre-located pair and frozen source windows under the predeclared limits; no hidden review paper | Candidate field-by-field comparison with source anchors |
| Source-bound semantic audit | The *same* pre-located pair and windows; byte-locked extraction, field-by-field comparison, explicit abstention | Local checked relation and unresolved scope; no qualification |
| Kernel replay (separate track) | A pinned Lean build or replay, when dependencies can be retrieved and bounded | Whether the *formal declaration* passed that kernel run; no manuscript identity |

If a formal project cannot be built within the frozen budget, kernel replay returns `NOT_RUN` or `INDETERMINATE`; it does not receive a guessed PASS. Replaying the kernel, where feasible, is valuable formal-validity evidence, but it is neither entered in the semantic confusion matrix nor scored as a missed alignment gap. A final report must show source identity, semantic result, proof-method result, kernel status, and human review status in distinct fields; an `ALIGNED_UNDER_CHECKED_SCOPE` semantic result plus kernel PASS does not automatically settle the overall paper or target problem.

The present pilot executes only the source-bound **development** method for Lemma 8.6. The pressure-flux and trace-square records are manual source audits and controls, not additional runs of that detector. The trace-square positive control's fresh Mathlib build stopped during dependency resolution, before its theorem was checked. None of these records claims a completed withheld semantic comparison or formal replay.

## Measures and reporting

Report per case before any aggregate. For the two semantic methods, score verified gap detections and misses, false gap reports, false `ALIGNED_UNDER_CHECKED_SCOPE` decisions, abstention and its cause, exact anchor correctness, and same-statement/different-method false alarms. An unsupported guess is not a verified true positive. Score any declaration-location experiment separately. For kernel replay, report declaration identity, toolchain/dependency closure, actual PASS/FAIL/`NOT_RUN`/`INDETERMINATE`, and its execution cost; do not add its result to semantic precision or recall.

Measure human pre-location, source preparation, and adjudication time separately from each method's run time. Report wall-clock time, model calls, prompt/completion tokens and usage status per call (`UNKNOWN` rather than invented zero), actual vendor charge if available, estimated charge separately, and source/dependency setup cost. Record source-bound tool and human costs even when token use is zero. Keep statement alignment, proof-method alignment, kernel validity, and integrated-report scope separate. Report each case's provenance, reference-label process, public exposure, and any unblinding or disagreement. With a small cohort, do not infer a population-level winner or general mathematical reliability.

Any positive alignment result is limited to the checked fields and source versions. No arm may issue a QualificationReceipt, alter a CurrentUseBinding or AuthorizationGrant, or claim that a Clay problem has been solved. A later mathematical adjudication or a different source revision gets a new record rather than rewriting the old prediction.
