import { FormEvent, useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, FileCheck2, GitBranch, LoaderCircle, RefreshCw, Shield } from "lucide-react";

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Claim = {
  id: string; claim_key: string; claim_type: string; revision_number: number;
  statement: string; scope: string; semantic_hash: string;
  verification_attempts?: { id: string; validation_modality: string; outcome: string; created_at: string }[];
};
type Receipt = { id: string; profile_id: string; verdict: string; receipt_hash: string; issued_at: string; blockers: string[] };
type Binding = { id: string; profile_id: string; qualification_receipt_id: string; state: string; stale_reason?: string | null };
type ClaimStatus = {
  claim: Claim;
  evaluations: { id: string; profile_id: string; verdict: string; blockers: string[]; criteria: { code: string; state: string; reason: string }[]; created_at: string }[];
  receipts: Receipt[];
  knowledge_admissions: { id: string; qualification_receipt_id: string; approved_by: string; issued_at: string }[];
  current_use_bindings: Binding[];
};
type Notice = { id: string; source_commit: string; source_hash: string; source_artifact_id: string; verification_hash: string; verified_at: string };
type Decision = {
  id: string; target_scope: string; target_receipt_id?: string | null; target_attempt_id?: string | null;
  reason_code: string; rationale: string; effect_state: string; decision_hash: string;
  affected_binding_ids: string[]; decided_at: string;
};
type TargetScope = "claim_revision" | "receipt" | "proof_attempt";

const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed";
const short = (value: string) => value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
const date = (value: string) => new Date(value).toLocaleString();

export function QualificationFlow({ api, target, isAdmin, onOpenResearch, onOpenRun }: {
  api: Api;
  target: { key: number; claimId: string } | null;
  isAdmin: boolean;
  onOpenResearch: (claimId: string) => void;
  onOpenRun: (runId: string) => void;
}) {
  const [claimInput, setClaimInput] = useState(target?.claimId || "");
  const [status, setStatus] = useState<ClaimStatus | null>(null);
  const [claimDetail, setClaimDetail] = useState<Claim | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [profile, setProfile] = useState("math.formal.v1");
  const [profiles, setProfiles] = useState<{ profile_id: string }[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [candidate, setCandidate] = useState({ claim_key: "", name: "", statement: "", scope: "" });
  const [kernel, setKernel] = useState({ backend: "lean", declaration_name: "", source: "" });
  const [admissionReceipt, setAdmissionReceipt] = useState("");
  const [admissionRationale, setAdmissionRationale] = useState("");
  const [sourceCommit, setSourceCommit] = useState("");
  const [noticeInput, setNoticeInput] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [scope, setScope] = useState<TargetScope>("claim_revision");
  const [receiptId, setReceiptId] = useState("");
  const [attemptId, setAttemptId] = useState("");
  const [rationale, setRationale] = useState("");

  const load = async (id: string) => {
    const claimId = id.trim();
    if (!claimId) return;
    setBusy("load"); setError(""); setMessage("");
    try {
      const [nextStatus, nextDetail, nextDecisions] = await Promise.all([
        api(`/api/qualification/claims/${encodeURIComponent(claimId)}/status`) as Promise<ClaimStatus>,
        api(`/api/research-registry/claims/${encodeURIComponent(claimId)}`) as Promise<Claim>,
        api(`/api/qualification/claims/${encodeURIComponent(claimId)}/invalidation-decisions`) as Promise<Decision[]>,
      ]);
      setStatus(nextStatus); setClaimDetail(nextDetail); setDecisions(nextDecisions);
      setClaimInput(claimId);
      setAdmissionReceipt(nextStatus.receipts.at(-1)?.id || "");
      setReceiptId(nextStatus.receipts.at(-1)?.id || "");
      setAttemptId(nextDetail.verification_attempts?.filter(item => item.validation_modality === "kernel_check").at(-1)?.id || "");
    } catch (reason) { setStatus(null); setClaimDetail(null); setDecisions([]); setError(errorText(reason)); }
    finally { setBusy(""); }
  };

  useEffect(() => {
    void api("/api/qualification/profiles").then((value: { profile_id: string }[]) => setProfiles(value)).catch(() => {});
  }, []);
  useEffect(() => { if (target?.claimId) void load(target.claimId); }, [target?.key]);

  const execute = async (name: string, action: () => Promise<unknown>, success: string) => {
    setBusy(name); setError(""); setMessage("");
    try {
      const result = await action();
      if (status?.claim.id) await load(status.claim.id);
      setMessage(success);
      return result;
    } catch (reason) { setError(errorText(reason)); return null; }
    finally { setBusy(""); }
  };

  const register = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("register"); setError(""); setMessage("");
    try {
      const result = await api("/api/qualification/math-theorems", {
        method: "POST", body: JSON.stringify(candidate),
      }) as { claim: Claim };
      await load(result.claim.id);
      setMessage("Candidate stored. No qualification or knowledge admission was created.");
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  };

  const verifyKernel = async (event: FormEvent) => {
    event.preventDefault();
    if (!status) return;
    const result = await execute("kernel", () => api(`/api/qualification/claims/${status.claim.id}/kernel-verifications`, {
      method: "POST", body: JSON.stringify(kernel),
    }), "Kernel attempt recorded. Evaluate the frozen claim separately.") as { run?: { id: string } } | null;
    if (result?.run?.id) onOpenRun(result.run.id);
  };

  const evaluate = async () => {
    if (!status) return;
    await execute("evaluate", () => api(`/api/qualification/claims/${status.claim.id}/evaluations`, {
      method: "POST", body: JSON.stringify({ profile_id: profile }),
    }), "Gate evaluation saved. An ADMITTED receipt does not enter current knowledge automatically.");
  };

  const admit = async (event: FormEvent) => {
    event.preventDefault();
    if (!status || !admissionReceipt) return;
    if (!window.confirm(`Admit receipt ${admissionReceipt} to the current knowledge view? This is a separate authority transition.`)) return;
    await execute("admit", () => api(`/api/qualification/receipts/${admissionReceipt}/knowledge-admissions`, {
      method: "POST", body: JSON.stringify({ admission_policy_id: "knowledge.default.v1", rationale: admissionRationale }),
    }), "Knowledge admission recorded and current-use binding refreshed.");
  };

  const verifyNotice = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("notice"); setError(""); setMessage("");
    try {
      const value = await api("/api/qualification/invalidation-notices/verify", {
        method: "POST", body: JSON.stringify({ source_commit: sourceCommit.trim() }),
      }) as Notice;
      setNotice(value); setNoticeInput(value.id);
      setMessage("Notice snapshot verified and frozen. No local authority was changed.");
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  };

  const loadNotice = async () => {
    setBusy("notice"); setError("");
    try { setNotice(await api(`/api/qualification/invalidation-notices/${encodeURIComponent(noticeInput.trim())}`) as Notice); }
    catch (reason) { setNotice(null); setError(errorText(reason)); }
    finally { setBusy(""); }
  };

  const decide = async (event: FormEvent) => {
    event.preventDefault();
    if (!status || !notice) return;
    const targetText = scope === "claim_revision" ? `revision ${status.claim.id}` : scope === "receipt" ? `receipt ${receiptId}` : `attempt ${attemptId} in receipt ${receiptId}`;
    if (!window.confirm(`Apply a ${scope === "claim_revision" ? "REVOKED" : "STALE"} invalidation to ${targetText}? Historical receipts remain immutable. This cannot be undone by this page.`)) return;
    const reason_code = scope === "claim_revision" ? "WITHDRAWN_UPSTREAM_CONSTRUCTION" : scope === "receipt" ? "RECEIPT_DEFECT" : "PROOF_INVALIDATED";
    await execute("decide", () => api("/api/qualification/invalidation-decisions", {
      method: "POST", body: JSON.stringify({
        notice_verification_id: notice.id,
        target_claim_revision_id: status.claim.id,
        target_claim_semantic_hash: status.claim.semantic_hash,
        target_scope: scope, reason_code, rationale,
        target_receipt_id: scope === "claim_revision" ? null : receiptId,
        target_attempt_id: scope === "proof_attempt" ? attemptId : null,
      }),
    }), "Decision recorded. Current-use effects are listed below; dependent bindings refresh on read.");
  };

  const selectedReceipt = status?.receipts.find(item => item.id === receiptId);
  const current = status?.current_use_bindings.find(item => item.profile_id === profile);
  const admitted = status?.receipts.some(item => item.verdict === "ADMITTED");
  const kernelAttempts = claimDetail?.verification_attempts?.filter(item => item.validation_modality === "kernel_check") || [];

  return <section className="panel qualification-flow">
    <header className="topbar"><div><span className="eyebrow">QUALIFICATION PLANE</span><h1>Claim authority flow</h1><p className="topbar-copy">Evidence can earn a qualification. Only a separate admission can make it current knowledge; only an authorized decision can invalidate it.</p></div><Shield size={27} /></header>
    <div className="qualification-workspace">
      <div className="qualification-path" aria-label="Authority stages"><span>Candidate</span><ArrowRight size={14} /><span>Kernel evidence</span><ArrowRight size={14} /><span>Qualification</span><ArrowRight size={14} /><span>Knowledge admission</span><ArrowRight size={14} /><span>Current use</span></div>
      <form className="qualification-lookup" onSubmit={event => { event.preventDefault(); void load(claimInput); }}><label><span>ClaimRevision ID</span><input value={claimInput} onChange={event => setClaimInput(event.target.value)} placeholder="Paste a frozen claim revision ID" required /></label><button className="secondary" type="submit" disabled={Boolean(busy)}>{busy === "load" ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}Load revision</button></form>
      {error && <div className="qualification-alert error" role="alert">{error}</div>}
      {message && <div className="qualification-alert success" role="status"><CheckCircle2 size={16} />{message}</div>}

      {!status && isAdmin && <details className="qualification-card" open><summary>1 · Register a mathematical candidate</summary><form className="qualification-form" onSubmit={register}><div className="qualification-grid"><label><span>Claim key</span><input value={candidate.claim_key} onChange={event => setCandidate({ ...candidate, claim_key: event.target.value })} required /></label><label><span>Name</span><input value={candidate.name} onChange={event => setCandidate({ ...candidate, name: event.target.value })} required /></label></div><label><span>Exact statement</span><textarea rows={3} value={candidate.statement} onChange={event => setCandidate({ ...candidate, statement: event.target.value })} required /></label><label><span>Scope and assumptions</span><textarea rows={2} value={candidate.scope} onChange={event => setCandidate({ ...candidate, scope: event.target.value })} required /></label><button className="primary" disabled={Boolean(busy)}>Store candidate only</button><small>This does not create a receipt, binding, or execution grant.</small></form></details>}

      {status && <>
        <article className="qualification-card qualification-claim"><div className="qualification-card-heading"><div><span className="eyebrow">FROZEN SUBJECT · r{status.claim.revision_number}</span><h2>{status.claim.claim_key}</h2></div><button className="text-button" onClick={() => onOpenResearch(status.claim.id)}>Claim Explorer <ArrowRight size={13} /></button></div><p>{status.claim.statement}</p><small>{status.claim.scope}</small><code title={status.claim.semantic_hash}>semantic hash {status.claim.semantic_hash}</code></article>
        <div className="qualification-grid">
          <article className="qualification-card"><h2><FileCheck2 size={17} /> 2 · Verification</h2><p>{kernelAttempts.length} kernel attempt(s) on this exact revision.</p>{kernelAttempts.slice().reverse().map(item => <div className="qualification-record" key={item.id}><span>{item.outcome}</span><code title={item.id}>{short(item.id)}</code><small>{date(item.created_at)}</small></div>)}{isAdmin && <details className="qualification-action"><summary>Run Lean / Coq kernel</summary><form className="qualification-form" onSubmit={verifyKernel}><div className="qualification-grid"><label><span>Backend</span><select value={kernel.backend} onChange={event => setKernel({ ...kernel, backend: event.target.value })}><option value="lean">Lean</option><option value="coq">Coq</option></select></label><label><span>Declaration</span><input value={kernel.declaration_name} onChange={event => setKernel({ ...kernel, declaration_name: event.target.value })} required /></label></div><label><span>Source</span><textarea rows={8} value={kernel.source} onChange={event => setKernel({ ...kernel, source: event.target.value })} required /></label><button className="secondary" disabled={Boolean(busy)}>Run kernel verification</button></form></details>}</article>
          <article className="qualification-card"><h2><Shield size={17} /> 3 · Qualification Gate</h2><label className="qualification-select"><span>Profile</span><select value={profile} onChange={event => setProfile(event.target.value)}>{profiles.length ? profiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.profile_id}</option>) : <option value="math.formal.v1">math.formal.v1</option>}</select></label><p>{status.evaluations.length} evaluation(s); {status.receipts.length} immutable receipt(s).</p>{status.evaluations.slice().reverse().map(item => <details className="qualification-record" key={item.id}><summary><strong>{item.verdict}</strong><code title={item.id}>{short(item.id)}</code></summary><small>{item.blockers.join(", ") || "No blockers"}</small>{item.criteria.map(criterion => <div className="qualification-criterion" key={criterion.code}><strong>{criterion.code} · {criterion.state}</strong><small>{criterion.reason}</small></div>)}</details>)}{isAdmin && <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => void evaluate()}>Evaluate frozen evidence</button>}<small>ADMITTED qualifies this revision under one profile; it does not create a current-use binding.</small></article>
        </div>
        <div className="qualification-grid">
          <article className="qualification-card"><h2><GitBranch size={17} /> 4 · Knowledge Admission</h2><p>{status.knowledge_admissions.length} admission receipt(s). Qualification does not imply admission.</p>{status.receipts.slice().reverse().map(item => <div className="qualification-record" key={item.id}><strong>{item.verdict}</strong><code title={item.id}>{short(item.id)}</code><small>{item.profile_id} · hash {short(item.receipt_hash)}</small></div>)}{isAdmin && <form className="qualification-form" onSubmit={admit}><label><span>ADMITTED receipt</span><select value={admissionReceipt} onChange={event => setAdmissionReceipt(event.target.value)} required><option value="">Select receipt</option>{status.receipts.filter(item => item.verdict === "ADMITTED").map(item => <option key={item.id} value={item.id}>{short(item.id)} · {item.profile_id}</option>)}</select></label><label><span>Admission rationale</span><textarea rows={2} value={admissionRationale} onChange={event => setAdmissionRationale(event.target.value)} required /></label><button className="secondary" disabled={Boolean(busy) || !admissionReceipt || !admitted}>Admit to knowledge view</button></form>}</article>
          <article className="qualification-card"><h2><CheckCircle2 size={17} /> 5 · Current Use</h2>{status.current_use_bindings.length ? status.current_use_bindings.map(item => <div className="qualification-binding" key={item.id}><strong className={item.state === "current" ? "is-current" : "is-stale"}>{item.state.toUpperCase()}</strong><span>{item.profile_id}</span><code title={item.qualification_receipt_id}>receipt {short(item.qualification_receipt_id)}</code>{item.stale_reason && <small>{item.stale_reason}</small>}</div>) : <p>No current-use binding. Qualified receipts remain historical evidence only.</p>}{current?.state === "current" && <small>Current knowledge is a separate authority from tool execution.</small>}</article>
        </div>
        <article className="qualification-card qualification-invalidation"><h2><AlertTriangle size={18} /> Verified Invalidation Decision</h2><p>A verified external notice is evidence, not a local withdrawal. The workspace administrator must bind a decision to this exact revision, receipt, or proof attempt.</p>
          {isAdmin && <div className="qualification-grid"><form className="qualification-form" onSubmit={verifyNotice}><h3>1 · Verify pinned notice</h3><label><span>openai/math commit (40 hex)</span><input pattern="[0-9a-f]{40}" value={sourceCommit} onChange={event => setSourceCommit(event.target.value)} required /></label><button className="secondary" disabled={Boolean(busy)}>Fetch and freeze history.md</button><small>Byte/hash snapshot only; Git signature and mathematical interpretation are not certified.</small></form><div className="qualification-form"><h3>Or load a prior verification</h3><label><span>Notice verification ID</span><input value={noticeInput} onChange={event => setNoticeInput(event.target.value)} /></label><button className="secondary" type="button" disabled={Boolean(busy) || !noticeInput.trim()} onClick={() => void loadNotice()}>Load verification</button></div></div>}
          {notice && <div className="qualification-notice"><strong>Verified source snapshot</strong><span>openai/math @ {notice.source_commit}</span><code>SHA-256 {notice.source_hash}</code><small>Artifact {notice.source_artifact_id} · verified {date(notice.verified_at)}</small></div>}
          {isAdmin && notice && <form className="qualification-form qualification-decision" onSubmit={decide}><h3>2 · Review exact target and effect</h3><div className="qualification-grid"><label><span>Target scope</span><select value={scope} onChange={event => setScope(event.target.value as TargetScope)}><option value="claim_revision">Entire frozen ClaimRevision</option><option value="receipt">One QualificationReceipt</option><option value="proof_attempt">One selected kernel attempt</option></select></label>{scope !== "claim_revision" && <label><span>Receipt</span><select value={receiptId} onChange={event => setReceiptId(event.target.value)} required><option value="">Select receipt</option>{status.receipts.map(item => <option key={item.id} value={item.id}>{short(item.id)} · {item.profile_id}</option>)}</select></label>}</div>{scope === "proof_attempt" && <label><span>Kernel attempt in selected receipt closure</span><select value={attemptId} onChange={event => setAttemptId(event.target.value)} required><option value="">Select attempt</option>{kernelAttempts.map(item => <option key={item.id} value={item.id}>{short(item.id)} · {item.outcome}</option>)}</select></label>}<label><span>Decision rationale</span><textarea rows={3} value={rationale} onChange={event => setRationale(event.target.value)} required /></label><div className="qualification-preview"><strong>Decision preview</strong><span>Revision: {status.claim.id}</span><span>Semantic hash: {status.claim.semantic_hash}</span><span>Target: {scope === "claim_revision" ? "whole revision" : scope === "receipt" ? `receipt ${receiptId || "not selected"}` : `attempt ${attemptId || "not selected"} · receipt ${receiptId || "not selected"}`}</span><span>Expected direct effect: {scope === "claim_revision" ? "REVOKED" : "STALE"} on matching current binding; downstream taint refreshes on read.</span><small>Server re-checks target, hash, evidence, and authority atomically. Exact affected bindings are returned only after the decision.</small></div><button className="primary danger" disabled={Boolean(busy) || !rationale.trim() || (scope !== "claim_revision" && !selectedReceipt) || (scope === "proof_attempt" && !attemptId)}>Confirm invalidation decision</button></form>}
          <div className="qualification-history"><h3>Decision history · {decisions.length}</h3>{decisions.length ? decisions.slice().reverse().map(item => <div className="qualification-record" key={item.id}><strong>{item.effect_state.toUpperCase()} · {item.reason_code}</strong><span>{item.target_scope} · {item.rationale}</span><code title={item.decision_hash}>decision hash {item.decision_hash}</code><small>{date(item.decided_at)} · {item.affected_binding_ids.length} direct binding(s)</small></div>) : <p>No local invalidation decision. An upstream notice alone cannot alter current use.</p>}</div>
        </article>
      </>}
      {!status && !isAdmin && <div className="qualification-card"><p>Enter a ClaimRevision ID to inspect its qualification, current-use, and invalidation history. Mutating actions require a workspace administrator.</p></div>}
    </div>
  </section>;
}
