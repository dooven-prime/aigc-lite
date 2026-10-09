import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, FileCheck2, LoaderCircle, RefreshCw, Upload } from "lucide-react";

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Importer = {
  importer_id: string;
  version: number;
  display_name: string;
  description: string;
  source_format: string;
  target_surface: string;
  preview_endpoint: string | null;
  commit_endpoint: string;
  importer_contract: string;
  qualification_granted: boolean;
  knowledge_admitted: boolean;
};
type MathPreview = {
  source_commit: string;
  preview_hash: string;
  manifest_hash: string;
  family_count: number;
  manuscript_count: number;
  lean_linked_family_count: number;
};
type MathImport = { id: string; source_commit: string; family_count: number; manuscript_count: number; deduplicated?: boolean };

const message = (error: unknown) => error instanceof Error ? error.message : "Request failed";

export function ResearchImports({ api, isAdmin, onOpenClaim }: {
  api: Api;
  isAdmin: boolean;
  onOpenClaim: (claimId: string) => void;
}) {
  const [importers, setImporters] = useState<Importer[]>([]);
  const [mathImports, setMathImports] = useState<MathImport[]>([]);
  const [frontierName, setFrontierName] = useState("AI Frontier Claim Registry");
  const [registryFile, setRegistryFile] = useState<File | null>(null);
  const [ledgerFile, setLedgerFile] = useState<File | null>(null);
  const [sourceCommit, setSourceCommit] = useState("");
  const [preview, setPreview] = useState<MathPreview | null>(null);
  const [frontierClaimId, setFrontierClaimId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");

  const load = async () => {
    setError("");
    try {
      const [registered, batches] = await Promise.all([
        api("/api/research/importers") as Promise<Importer[]>,
        api("/api/research/math-release-imports") as Promise<MathImport[]>,
      ]);
      setImporters(registered);
      setMathImports(batches);
    } catch (reason) { setError(message(reason)); }
  };
  useEffect(() => { void load(); }, []);

  const importFrontier = async (event: FormEvent) => {
    event.preventDefault();
    if (!registryFile) return;
    setBusy("frontier"); setError(""); setResult("");
    const body = new FormData();
    body.set("source_name", frontierName);
    body.set("registry_file", registryFile);
    if (ledgerFile) body.set("source_ledger_file", ledgerFile);
    try {
      const value = await api("/api/research-registry/import/frontier", {
        method: "POST", body,
      }) as { case: { id: string }; claims: { id: string }[]; imported: boolean };
      setFrontierClaimId(value.claims[0]?.id || "");
      setResult(value.imported ? "Frontier registry frozen. Claims remain candidates." : "Identical Frontier snapshot already exists; no duplicate was created.");
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(""); }
  };

  const previewMath = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("math-preview"); setError(""); setResult(""); setPreview(null);
    try {
      setPreview(await api("/api/research/math-release-imports/preview", {
        method: "POST", body: JSON.stringify({ source_commit: sourceCommit.trim() }),
      }) as MathPreview);
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(""); }
  };

  const commitMath = async () => {
    if (!preview) return;
    if (!window.confirm(`Store the pinned OpenAI mathematics catalogue at ${preview.source_commit} as candidate-only? This does not create ClaimRevisions or qualification.`)) return;
    setBusy("math-commit"); setError(""); setResult("");
    try {
      const committed = await api("/api/research/math-release-imports", {
        method: "POST",
        body: JSON.stringify({
          source_commit: preview.source_commit,
          expected_preview_hash: preview.preview_hash,
        }),
      }) as MathImport;
      setResult(`Candidate catalogue ${committed.id} ${committed.deduplicated ? "already existed" : "stored"}; no theorem was qualified.`);
      setPreview(null);
      await load();
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(""); }
  };

  return <section className="panel research-imports-panel">
    <header className="topbar"><div><span className="eyebrow">VERSIONED SOURCE ADAPTERS</span><h1>Research imports</h1><p className="topbar-copy">Every source has its own format contract and destination. Import is storage admission, never qualification or knowledge admission.</p></div><button className="secondary" onClick={() => void load()} disabled={Boolean(busy)}><RefreshCw size={15} />Refresh</button></header>
    <div className="research-imports-workspace">
      {error && <div className="qualification-alert error" role="alert">{error}</div>}
      {result && <div className="qualification-alert success" role="status">{result}{frontierClaimId && <button className="text-button" onClick={() => onOpenClaim(frontierClaimId)}>Open claim <ArrowRight size={13} /></button>}</div>}
      {!importers.length && !error && <p>Loading registered importers…</p>}
      {importers.map(importer => <article className="research-import-card" key={`${importer.importer_id}@${importer.version}`}>
        <div className="research-import-heading"><div><span className="eyebrow">{importer.importer_id}@{importer.version}</span><h2>{importer.display_name}</h2></div><span className="research-import-target">{importer.target_surface === "catalogue_candidate_only" ? "Catalogue candidate only" : "Claim candidate only"}</span></div>
        <p>{importer.description}</p>
        <div className="research-import-facts"><span>Format: {importer.source_format}</span><span>Contract: {importer.importer_contract}</span><span>Qualification: {importer.qualification_granted ? "granted" : "never on import"}</span><span>Knowledge admission: {importer.knowledge_admitted ? "granted" : "never on import"}</span></div>
        {importer.importer_id === "frontier.registry" && isAdmin && <form className="research-import-form" onSubmit={importFrontier}><h3>Import one Frontier-format registry</h3><label><span>Name</span><input value={frontierName} onChange={event => setFrontierName(event.target.value)} required /></label><div className="qualification-grid"><label><span>claim-registry.json</span><input type="file" accept="application/json,.json" onChange={event => setRegistryFile(event.target.files?.[0] || null)} required /></label><label><span>public-source-ledger.md · optional</span><input type="file" accept="text/markdown,.md" onChange={event => setLedgerFile(event.target.files?.[0] || null)} /></label></div><button className="primary" disabled={Boolean(busy) || !registryFile}>{busy === "frontier" ? <LoaderCircle size={14} className="spin" /> : <Upload size={14} />}Validate & freeze</button><small>Current Frontier adapter validates schema and declared source digests. It does not fetch or independently verify source contents.</small></form>}
        {importer.importer_id === "openai.math" && <div className="research-import-math"><div className="research-import-history"><strong>{mathImports.length} candidate catalogue(s) stored</strong>{mathImports.slice(0, 5).map(item => <code key={item.id}>{item.source_commit.slice(0, 12)} · {item.family_count} families · {item.manuscript_count} manuscripts</code>)}</div>{isAdmin && <form className="research-import-form" onSubmit={previewMath}><h3>Preview pinned upstream catalogue</h3><label><span>Exact 40-character Git commit</span><input pattern="[0-9a-f]{40}" value={sourceCommit} onChange={event => { setSourceCommit(event.target.value); setPreview(null); }} required /></label><button className="secondary" disabled={Boolean(busy)}>{busy === "math-preview" ? <LoaderCircle size={14} className="spin" /> : <FileCheck2 size={14} />}Preview source hashes</button></form>}{preview && <div className="research-import-preview"><strong>Candidate-only preview</strong><span>{preview.family_count} families · {preview.manuscript_count} manuscripts · {preview.lean_linked_family_count} Lean-linked families</span><code>manifest {preview.manifest_hash}</code><code>preview {preview.preview_hash}</code><small>Commit re-fetches the pinned bytes and rejects a changed preview. It creates no ClaimRevision, receipt or binding.</small><button className="primary" type="button" disabled={Boolean(busy)} onClick={() => void commitMath()}>Commit candidate catalogue</button></div>}</div>}
        {!isAdmin && <small>Viewing import history and adapter contracts is read-only; commit requires a workspace administrator.</small>}
      </article>)}
    </div>
  </section>;
}
