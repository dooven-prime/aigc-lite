import { FormEvent, useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Ban,
  Beaker,
  BookOpen,
  Bot,
  Boxes,
  Braces,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  GitBranch,
  Link2,
  ListTree,
  LoaderCircle,
  LogIn,
  LogOut,
  MessageSquare,
  Microscope,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Shield,
  Upload,
  Users,
  Wrench,
  XCircle,
} from "lucide-react";

type Session = { id: string; title: string; messages?: Message[] };
type Message = { role: string; content: string };
type User = { id: string; tenant_id: string; email: string; name: string; role: string };
type Panel = "chat" | "search" | "runs" | "decisions" | "research" | "knowledge" | "admin" | "models";
type Api = (path: string, init?: RequestInit) => Promise<any>;

type SearchKind = "message" | "document" | "run_step" | "artifact" | "citation" | "decision_case" | "research_claim";
type SearchResult = {
  id: string;
  kind: SearchKind;
  title: string;
  content: string;
  score: number;
  created_at: string;
  session_id?: string | null;
  run_id?: string | null;
  step_id?: string | null;
  artifact_id?: string | null;
  source_kind?: string | null;
};
type RunJump = {
  key: number;
  runId: string;
  stepId?: string;
  artifactId?: string;
};
type DecisionJump = { key: number; caseId: string };
type ResearchJump = { key: number; claimId: string };

type RunStatus = "running" | "succeeded" | "failed" | "cancelled" | "limit_reached";
type RunStep = {
  id: string;
  sequence: number;
  kind: "agent" | "model" | "tool" | "retrieval";
  name: string;
  status: string;
  input_content: string;
  output_content: string;
  metadata: Record<string, unknown>;
  created_at: string;
};
type Artifact = {
  id: string;
  name: string;
  kind: string;
  media_type: string;
  content_text: string;
  uri?: string | null;
  size_bytes: number;
  version: number;
  step_id?: string | null;
  created_at: string;
};
type Citation = {
  id: string;
  title: string;
  source_kind: string;
  source_id?: string | null;
  source_uri?: string | null;
  excerpt: string;
  locator: Record<string, unknown>;
  artifact_id?: string | null;
  step_id?: string | null;
  created_at: string;
};
type AgentRun = {
  id: string;
  session_id: string;
  request_id: string;
  status: RunStatus;
  requested_model?: string | null;
  selected_model: string;
  error_code?: string | null;
  created_at: string;
  completed_at?: string | null;
  steps?: RunStep[];
  artifacts?: Artifact[];
  citations?: Citation[];
};
type EvidenceArtifact = Artifact & { content_hash: string; metadata: Record<string, unknown>; preview?: string };
type EvidenceClaim = { id: string; statement: string; resolution: string; scope: string; prohibited_upgrades: string[] };
type EvidenceReview = { id: string; status: string; reviewer_kind: string; independent: boolean; finding: string };
type EvidenceReceipt = { id: string; status: string; run_id?: string | null; input_digest: string; output_digest: string; metadata: Record<string, any>; reviews: EvidenceReview[] };
type EvidenceProtocol = {
  id: string; name: string; profile: string; status: string; purpose: string; scope: string;
  content_hash: string; created_at: string; claims: EvidenceClaim[]; receipts: EvidenceReceipt[];
  artifacts: EvidenceArtifact[]; freezes: { id: string; name: string; version: number; content_hash: string; members: Record<string, unknown>[] }[];
};
type DecisionCase = {
  id: string; protocol_id: string; receipt_id: string; source_case_id: string; family: string;
  state_text: string; questions: Record<string, any>; answers: Record<string, any>; resolution: string;
  confidence: number; entropy: number; threshold: number; run_id?: string | null; step_id?: string | null;
  primary_artifact_id?: string | null; created_at: string;
};
type DecisionDashboard = {
  protocols: EvidenceProtocol[];
  protocol: EvidenceProtocol | null;
  statistics: { cases: number; decided: number; manual_review: number; undetermined: number; mean_confidence: number; mean_entropy: number; families: Record<string, number>; question_types: Record<string, number> };
  cases: DecisionCase[];
  imported?: boolean;
};
type ResearchSource = {
  id: string; source_key: string; locator: string; content_hash: string; status: string;
  metadata: Record<string, unknown>; created_at: string;
};
type ResearchClaim = {
  id: string; research_case_id: string; claim_key: string; revision_number: number;
  statement: string; claim_type: string; scope: string; method_revision: string;
  lifecycle_status: string; promotion_stage: "registered" | "evidence_ready" | "review_ready" | "release_ready" | "withdrawn";
  status_axes: { uncertainty?: string; causal?: string };
  closure_status: "closed" | "blocked"; blockers: string[]; sources: ResearchSource[];
  relations?: ResearchRelation[]; verification_attempts?: VerificationAttempt[]; verification_plans?: VerificationPlan[];
  verification_executions?: VerificationExecution[]; promotion_evaluations?: PromotionEvaluation[];
  created_at: string;
};
type ResearchRelation = {
  id: string; source_claim_id: string; target_claim_id: string; source_claim_key: string; target_claim_key: string;
  relation_type: "supports" | "refutes" | "depends_on" | "qualifies"; status: string; rationale: string;
  evidence_refs: string[]; withdrawal_reason?: string | null; withdrawn_at?: string | null; created_at: string;
};
type VerificationAttempt = {
  id: string; receipt_id: string; kind: string; outcome: string; method: string; scope: string;
  independent: boolean; input_digest: string; output_digest: string; artifact_ids: string[];
  plan_id?: string | null; verification_execution_id?: string | null; created_at: string;
};
type VerificationPlan = {
  id: string; plan_key: string; version: number; status: "active" | "retired"; executor: "agent";
  name: string; kind: string; method: string; scope: string; prompt: string; system_prompt: string;
  model?: string | null; result_contract_version: string; auto_promote: boolean; content_digest: string; created_at: string;
};
type VerificationExecution = {
  id: string; plan_id: string; plan_version: number; status: string; request_id: string; run_id?: string | null;
  scheduled_task_id?: string | null; attempt_id?: string | null; artifact_id?: string | null;
  promotion_evaluation_id?: string | null; outcome?: string | null; input_digest: string;
  output_digest?: string | null; error_code?: string | null; started_at: string; completed_at?: string | null;
};
type PromotionCriterion = { code: string; passed: boolean; observed: unknown };
type PromotionEvaluation = {
  id: string; from_stage: string; target_stage: string; decision: "passed" | "blocked"; policy_version: string;
  input_digest: string; evaluation_digest: string; criteria: PromotionCriterion[]; blockers: string[]; created_at: string;
};
type ResearchCase = {
  id: string; protocol_id: string; receipt_id: string; profile: string; name: string;
  registry_id: string; registry_version: string; authority: string; as_of_date?: string | null;
  status: string; source_artifact_id: string; source_ledger_artifact_id?: string | null;
  metadata: Record<string, unknown>; created_at: string;
};
type ResearchDashboard = {
  cases: ResearchCase[]; case: ResearchCase | null; protocol: EvidenceProtocol | null;
  claims: ResearchClaim[]; relations: ResearchRelation[];
  statistics: { claims: number; closed: number; blocked: number; sources: number; claim_types: Record<string, number>; uncertainty_statuses: Record<string, number>; causal_statuses: Record<string, number>; promotion_stages: Record<string, number> };
  imported?: boolean;
};

const storedToken = localStorage.getItem("aigc-lite-token") || "";

export function App() {
  const [token, setToken] = useState(storedToken);
  const [user, setUser] = useState<User | null>(null);
  const [panel, setPanel] = useState<Panel>("chat");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(false);
  const [runJump, setRunJump] = useState<RunJump | null>(null);
  const [decisionJump, setDecisionJump] = useState<DecisionJump | null>(null);
  const [researchJump, setResearchJump] = useState<ResearchJump | null>(null);

  const api = async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...init, headers });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Request failed");
    return data;
  };

  const refresh = async () => {
    if (!token) return;
    try { setUser(await api("/api/auth/me")); setSessions(await api("/api/sessions")); }
    catch { logout(); }
  };
  useEffect(() => { void refresh(); }, [token]);

  const logout = () => { localStorage.removeItem("aigc-lite-token"); setToken(""); setUser(null); setSessions([]); setSession(null); };
  if (!token || !user) return <Login onLogin={(next) => { localStorage.setItem("aigc-lite-token", next); setToken(next); }} />;

  const openSession = async (id: string) => setSession(await api(`/api/sessions/${id}`));
  const createSession = async () => { const next = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) }); setSessions([next, ...sessions]); setSession({ ...next, messages: [] }); setPanel("chat"); };
  const openSessionFromSearch = async (id: string) => { await openSession(id); setPanel("chat"); };
  const openRunFromSearch = (result: SearchResult, target: "run" | "step" | "artifact") => {
    if (!result.run_id) return;
    setRunJump({
      key: Date.now(),
      runId: result.run_id,
      stepId: target === "step" ? result.step_id || undefined : undefined,
      artifactId: target === "artifact" ? result.artifact_id || undefined : undefined,
    });
    setPanel("runs");
  };
  const openDecision = (caseId: string) => {
    setDecisionJump({ key: Date.now(), caseId });
    setPanel("decisions");
  };
  const openResearchClaim = (claimId: string) => {
    setResearchJump({ key: Date.now(), claimId });
    setPanel("research");
  };
  const openRunById = (runId: string, stepId?: string, artifactId?: string) => {
    setRunJump({ key: Date.now(), runId, stepId, artifactId });
    setPanel("runs");
  };
  const send = async (prompt: string) => {
    if (!prompt.trim()) return;
    setLoading(true);
    try {
      const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ prompt, session_id: session?.id }) });
      const id = data.session_id as string;
      setSession((current) => ({ id, title: current?.title || prompt.slice(0, 30), messages: [...(current?.messages || []), { role: "user", content: prompt }, { role: "assistant", content: data.content }] }));
      setSessions(await api("/api/sessions"));
    } finally { setLoading(false); }
  };

  return <div className="app-shell">
    <nav className="sidebar">
      <div className="brand"><div className="brand-mark"><Bot size={18} /></div><div><strong>aigc-lite</strong><small>{user.name}</small></div></div>
      <button className="primary new-button" onClick={createSession}><Plus size={16} />New conversation</button>
      <div className="nav-group"><button className={panel === "chat" ? "nav active" : "nav"} onClick={() => setPanel("chat")}><Bot size={16} />Workspace</button><button className={panel === "search" ? "nav active" : "nav"} onClick={() => setPanel("search")}><Search size={16} />Search</button><button className={panel === "runs" ? "nav active" : "nav"} onClick={() => { setRunJump(null); setPanel("runs"); }}><ListTree size={16} />Runs</button><button className={panel === "decisions" ? "nav active" : "nav"} onClick={() => { setDecisionJump(null); setPanel("decisions"); }}><Braces size={16} />Decisions</button><button className={panel === "research" ? "nav active" : "nav"} onClick={() => { setResearchJump(null); setPanel("research"); }}><Microscope size={16} />Research</button><button className={panel === "knowledge" ? "nav active" : "nav"} onClick={() => setPanel("knowledge")}><BookOpen size={16} />Knowledge</button><button className={panel === "models" ? "nav active" : "nav"} onClick={() => setPanel("models")}><Settings size={16} />Models</button>{user.role === "admin" && <button className={panel === "admin" ? "nav active" : "nav"} onClick={() => setPanel("admin")}><Shield size={16} />Administration</button>}</div>
      {panel === "chat" && <><div className="section-label">Recent conversations</div><div className="session-list">{sessions.map(item => <button className={item.id === session?.id ? "session active" : "session"} key={item.id} onClick={() => void openSession(item.id)}>{item.title}</button>)}</div></>}
      <button className="logout" onClick={logout}><LogOut size={15} />Sign out</button>
    </nav>
    <main className="content">{panel === "chat" && <Chat session={session} loading={loading} onSend={send} onCreate={createSession} />}{panel === "search" && <WorkspaceSearch api={api} onOpenRun={openRunFromSearch} onOpenDecision={openDecision} onOpenResearch={openResearchClaim} onOpenSession={openSessionFromSearch} />}{panel === "runs" && <RunExplorer api={api} target={runJump} />}{panel === "decisions" && <DecisionLab api={api} target={decisionJump} isAdmin={user.role === "admin"} onOpenRun={openRunById} />}{panel === "research" && <ResearchRegistry api={api} target={researchJump} isAdmin={user.role === "admin"} onOpenRun={openRunById} />}{panel === "knowledge" && <Knowledge api={api} />}{panel === "models" && <Models api={api} />}{panel === "admin" && <Admin api={api} />}</main>
  </div>;
}

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [register, setRegister] = useState(false); const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [name, setName] = useState(""); const [workspace, setWorkspace] = useState(""); const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setError(""); try { const response = await fetch(`/api/auth/${register ? "register" : "login"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(register ? { email, password, name, workspace_name: workspace } : { email, password }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail); onLogin(data.access_token); } catch (e) { setError(e instanceof Error ? e.message : "Unable to sign in"); } };
  return <div className="auth-page"><form className="auth-card" onSubmit={submit}><div className="brand centered"><div className="brand-mark"><Bot size={20} /></div><div><strong>aigc-lite</strong><small>AI workspace</small></div></div><h1>{register ? "Create your workspace" : "Welcome back"}</h1>{register && <input placeholder="Workspace name" value={workspace} onChange={e => setWorkspace(e.target.value)} required />}{register && <input placeholder="Your name" value={name} onChange={e => setName(e.target.value)} required />}<input type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} required /><input type="password" placeholder="Password, 8+ characters" minLength={8} value={password} onChange={e => setPassword(e.target.value)} required />{error && <div className="error">{error}</div>}<button className="primary" type="submit"><LogIn size={16} />{register ? "Create account" : "Sign in"}</button><button className="text-button" type="button" onClick={() => setRegister(!register)}>{register ? "Already have an account? Sign in" : "Create a new workspace"}</button></form></div>;
}

function Chat({ session, loading, onSend, onCreate }: { session: Session | null; loading: boolean; onSend: (text: string) => Promise<void>; onCreate: () => Promise<void> }) { const [prompt, setPrompt] = useState(""); const submit = async (event: FormEvent) => { event.preventDefault(); const next = prompt; setPrompt(""); await onSend(next); }; return <div className="chat-view"><header className="topbar"><div><span className="eyebrow">WORKSPACE</span><h1>{session?.title || "Start a conversation"}</h1></div><span className="status-dot">Ready</span></header><div className="messages">{!session && <div className="empty"><div className="empty-icon"><Bot size={24} /></div><h2>What are you working on?</h2><p>Ask a question, analyze a document, or connect a tool.</p><button className="secondary" onClick={onCreate}><Plus size={15} />New conversation</button></div>}{session?.messages?.map((message, index) => <div className={message.role === "user" ? "message user" : "message assistant"} key={`${index}-${message.role}`}><div className="message-label">{message.role === "user" ? "You" : "aigc-lite"}</div><div>{message.content}</div></div>)}{loading && <div className="message assistant"><div className="message-label">aigc-lite</div><div className="typing">Thinking...</div></div>}</div><form className="composer" onSubmit={submit}><textarea value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Ask anything..." rows={2} /><button className="send" disabled={loading || !prompt.trim()} aria-label="Send"><Send size={17} /></button></form></div>; }

function WorkspaceSearch({
  api,
  onOpenRun,
  onOpenDecision,
  onOpenResearch,
  onOpenSession,
}: {
  api: Api;
  onOpenRun: (result: SearchResult, target: "run" | "step" | "artifact") => void;
  onOpenDecision: (caseId: string) => void;
  onOpenResearch: (claimId: string) => void;
  onOpenSession: (sessionId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [activeKind, setActiveKind] = useState<SearchKind | "all">("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const search = async (event: FormEvent) => {
    event.preventDefault();
    const nextQuery = query.trim();
    if (!nextQuery) return;
    setLoading(true);
    setError("");
    setSubmittedQuery(nextQuery);
    setActiveKind("all");
    try {
      setResults(await api(`/api/search?q=${encodeURIComponent(nextQuery)}&limit=50`) as SearchResult[]);
    } catch (reason) {
      setResults([]);
      setError(reason instanceof Error ? reason.message : "Unable to search the workspace");
    } finally {
      setLoading(false);
    }
  };

  const kindCounts = results.reduce<Record<string, number>>((counts, result) => {
    counts[result.kind] = (counts[result.kind] || 0) + 1;
    return counts;
  }, {});
  const visibleResults = activeKind === "all"
    ? results
    : results.filter(result => result.kind === activeKind);
  const filters: { id: SearchKind | "all"; label: string }[] = [
    { id: "all", label: "All" },
    { id: "message", label: "Conversations" },
    { id: "document", label: "Knowledge" },
    { id: "run_step", label: "Steps" },
    { id: "artifact", label: "Artifacts" },
    { id: "citation", label: "Citations" },
    { id: "decision_case", label: "Decisions" },
    { id: "research_claim", label: "Research claims" },
  ];

  return <section className="panel workspace-search-panel">
    <header className="topbar search-topbar">
      <div>
        <span className="eyebrow">WORKSPACE MEMORY</span>
        <h1>Search</h1>
        <p className="topbar-copy">Find conversations, knowledge, execution steps, artifacts, sources, and research claims.</p>
      </div>
    </header>
    <div className="search-workspace">
      <form className="global-search" onSubmit={search}>
        <Search size={20} />
        <input
          aria-label="Search all workspace memory"
          autoFocus
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="Search everything in this workspace"
        />
        <button className="primary" type="submit" disabled={loading || !query.trim()}>
          {loading ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
          Search
        </button>
      </form>

      {!submittedQuery && <div className="search-intro">
        <div className="search-intro-icon"><Search size={25} /></div>
        <h2>One search across the whole workspace</h2>
        <p>Execution results retain links back to their Run, Step, and Artifact so context is never lost.</p>
        <div className="search-scope-grid">
          <span><MessageSquare size={15} />Conversations</span>
          <span><BookOpen size={15} />Knowledge</span>
          <span><ListTree size={15} />Run steps</span>
          <span><Boxes size={15} />Artifacts & sources</span>
          <span><GitBranch size={15} />Research claims</span>
        </div>
      </div>}

      {submittedQuery && <>
        <div className="search-result-bar">
          <div><strong>{results.length}</strong><span>results for “{submittedQuery}”</span></div>
          <span>{loading ? "Searching…" : "Ranked by relevance"}</span>
        </div>
        <div className="search-filters" aria-label="Filter search results">
          {filters.map(filter => {
            const count = filter.id === "all" ? results.length : kindCounts[filter.id] || 0;
            return <button
              type="button"
              key={filter.id}
              className={activeKind === filter.id ? "active" : ""}
              onClick={() => setActiveKind(filter.id)}
              aria-pressed={activeKind === filter.id}
            >{filter.label}<span>{count}</span></button>;
          })}
        </div>
        {error && <div className="search-error"><AlertCircle size={17} />{error}</div>}
        {!loading && !error && !visibleResults.length && <div className="search-empty">
          <Search size={24} />
          <h2>No matching records</h2>
          <p>{results.length ? "Try another result type." : "Try broader terms or a different phrase."}</p>
        </div>}
        <div className="search-results" aria-live="polite">
          {visibleResults.map(result => <SearchResultCard
            key={`${result.kind}-${result.id}`}
            result={result}
            onOpenRun={onOpenRun}
            onOpenDecision={onOpenDecision}
            onOpenResearch={onOpenResearch}
            onOpenSession={onOpenSession}
          />)}
        </div>
      </>}
    </div>
  </section>;
}

function SearchResultCard({
  result,
  onOpenRun,
  onOpenDecision,
  onOpenResearch,
  onOpenSession,
}: {
  result: SearchResult;
  onOpenRun: (result: SearchResult, target: "run" | "step" | "artifact") => void;
  onOpenDecision: (caseId: string) => void;
  onOpenResearch: (claimId: string) => void;
  onOpenSession: (sessionId: string) => Promise<void>;
}) {
  return <article className="search-result-card">
    <div className={`search-result-icon ${result.kind}`}><SearchKindIcon kind={result.kind} /></div>
    <div className="search-result-main">
      <div className="search-result-heading">
        <div><span className="search-kind">{searchKindLabel(result.kind)}</span><h2>{result.title || "Untitled record"}</h2></div>
        <time>{formatDate(result.created_at)}</time>
      </div>
      <p>{previewText(result.content, 650)}</p>
      <div className="search-result-meta">
        <code>{shortId(result.id)}</code>
        {result.source_kind && <span>{humanize(result.source_kind)}</span>}
        {result.run_id && <span>Run {shortId(result.run_id)}</span>}
      </div>
      <div className="search-result-actions">
        {result.session_id && <button type="button" onClick={() => void onOpenSession(result.session_id!)}>Conversation<ArrowRight size={13} /></button>}
        {result.run_id && <button type="button" onClick={() => onOpenRun(result, "run")}>Run<ArrowRight size={13} /></button>}
        {result.run_id && result.step_id && <button type="button" onClick={() => onOpenRun(result, "step")}>Step<ArrowRight size={13} /></button>}
        {result.run_id && result.artifact_id && <button type="button" onClick={() => onOpenRun(result, "artifact")}>Artifact<ArrowRight size={13} /></button>}
        {result.kind === "decision_case" && <button type="button" onClick={() => onOpenDecision(result.id)}>Decision<ArrowRight size={13} /></button>}
        {result.kind === "research_claim" && <button type="button" onClick={() => onOpenResearch(result.id)}>Claim<ArrowRight size={13} /></button>}
      </div>
    </div>
  </article>;
}

function SearchKindIcon({ kind }: { kind: SearchKind }) {
  if (kind === "message") return <MessageSquare size={18} />;
  if (kind === "document") return <BookOpen size={18} />;
  if (kind === "run_step") return <ListTree size={18} />;
  if (kind === "artifact") return <Boxes size={18} />;
  if (kind === "decision_case") return <Braces size={18} />;
  if (kind === "research_claim") return <GitBranch size={18} />;
  return <Link2 size={18} />;
}

function searchKindLabel(kind: SearchKind) {
  if (kind === "message") return "Conversation";
  if (kind === "document") return "Knowledge";
  if (kind === "run_step") return "Run step";
  if (kind === "artifact") return "Artifact";
  if (kind === "decision_case") return "Decision case";
  if (kind === "research_claim") return "Research claim";
  return "Citation";
}

function RunExplorer({ api, target }: { api: Api; target: RunJump | null }) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentRun | null>(null);
  const [query, setQuery] = useState("");
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  const [focusTarget, setFocusTarget] = useState<RunJump | null>(target);

  const loadDetail = async (runId: string, nextFocus: RunJump | null = null) => {
    setSelectedId(runId);
    setFocusTarget(nextFocus);
    setLoadingDetail(true);
    setError("");
    try {
      setDetail(await api(`/api/runs/${runId}`));
    } catch (reason) {
      setDetail(null);
      setError(reason instanceof Error ? reason.message : "Unable to load the run");
    } finally {
      setLoadingDetail(false);
    }
  };

  const refresh = async (preferredTarget: RunJump | null = null) => {
    setLoadingRuns(true);
    setError("");
    try {
      const nextRuns = await api("/api/runs?limit=100") as AgentRun[];
      setRuns(nextRuns);
      const nextId = preferredTarget?.runId || (selectedId && nextRuns.some(item => item.id === selectedId)
        ? selectedId
        : nextRuns[0]?.id);
      if (nextId) await loadDetail(nextId, preferredTarget?.runId === nextId ? preferredTarget : null);
      else {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load runs");
    } finally {
      setLoadingRuns(false);
    }
  };

  useEffect(() => { void refresh(target); }, [target?.key]);

  const needle = query.trim().toLocaleLowerCase();
  const visibleRuns = runs.filter(run => !needle || [
    run.id,
    run.status,
    run.selected_model,
    run.requested_model || "",
    run.error_code || "",
  ].some(value => value.toLocaleLowerCase().includes(needle)));
  const completedCount = runs.filter(run => run.status === "succeeded").length;
  const attentionCount = runs.filter(run => ["failed", "limit_reached"].includes(run.status)).length;

  return <section className="panel run-explorer">
    <header className="topbar run-topbar">
      <div>
        <span className="eyebrow">EXECUTION MEMORY</span>
        <h1>Run Explorer</h1>
        <p className="topbar-copy">Inspect model turns, tool calls, sources, and produced artifacts.</p>
      </div>
      <button className="secondary refresh-button" onClick={() => void refresh(focusTarget)} disabled={loadingRuns}>
        <RefreshCw size={15} className={loadingRuns ? "spin" : ""} />Refresh
      </button>
    </header>
    <div className="run-overview">
      <div><Activity size={15} /><span>Total runs</span><strong>{runs.length}</strong></div>
      <div><CheckCircle2 size={15} /><span>Succeeded</span><strong>{completedCount}</strong></div>
      <div><AlertCircle size={15} /><span>Needs attention</span><strong>{attentionCount}</strong></div>
      <div><Boxes size={15} /><span>Selected artifacts</span><strong>{detail?.artifacts?.length || 0}</strong></div>
    </div>
    <div className="run-workbench">
      <aside className="run-index">
        <div className="run-search">
          <Search size={15} />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter model, status, or id" aria-label="Filter runs" />
        </div>
        <div className="run-index-label"><span>{visibleRuns.length} runs</span><span>Newest first</span></div>
        <div className="run-list">
          {loadingRuns && !runs.length && <LoadingState label="Loading runs" />}
          {!loadingRuns && !visibleRuns.length && <div className="run-list-empty">{runs.length ? "No runs match this filter." : "No runs have been recorded yet."}</div>}
          {visibleRuns.map(run => <button
            key={run.id}
            className={run.id === selectedId ? "run-row active" : "run-row"}
            onClick={() => void loadDetail(run.id)}
            aria-pressed={run.id === selectedId}
          >
            <span className={`run-state-dot ${run.status}`} />
            <span className="run-row-main">
              <span className="run-row-title"><strong>{run.selected_model || "Unknown model"}</strong><StatusBadge status={run.status} compact /></span>
              <span className="run-row-meta"><Clock3 size={12} />{formatDate(run.created_at)}<code>{shortId(run.id)}</code></span>
            </span>
            <ChevronRight size={15} />
          </button>)}
        </div>
      </aside>
      <div className="run-detail-shell">
        {error && <div className="run-error"><AlertCircle size={17} /><span>{error}</span></div>}
        {loadingDetail && <LoadingState label="Loading run detail" />}
        {!loadingDetail && detail && <RunDetail run={detail} focus={focusTarget} />}
        {!loadingDetail && !detail && !error && <div className="run-empty-detail"><ListTree size={28} /><h2>Select a run</h2><p>Run steps and outputs will appear here.</p></div>}
      </div>
    </div>
  </section>;
}

function RunDetail({ run, focus }: { run: AgentRun; focus: RunJump | null }) {
  const steps = run.steps || [];
  const artifacts = run.artifacts || [];
  const citations = run.citations || [];
  useEffect(() => {
    const anchor = focus?.artifactId
      ? `artifact-${focus.artifactId}`
      : focus?.stepId
        ? `step-${focus.stepId}`
        : `run-${run.id}`;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [run.id, focus?.key]);
  return <div className="run-detail" id={`run-${run.id}`}>
    <div className="run-detail-header">
      <div>
        <div className="run-detail-title"><StatusIcon status={run.status} /><h2>{run.selected_model || "Agent run"}</h2><StatusBadge status={run.status} /></div>
        <div className="run-identifiers"><code title={run.id}>{run.id}</code><span>Request {shortId(run.request_id)}</span></div>
      </div>
      <div className="run-duration"><Clock3 size={14} /><span>{formatDuration(run.created_at, run.completed_at)}</span></div>
    </div>
    {run.error_code && <div className="run-terminal-error"><AlertCircle size={16} /><div><strong>Run ended with an error</strong><code>{run.error_code}</code></div></div>}
    <div className="run-facts">
      <div><span>Started</span><strong>{formatDate(run.created_at, true)}</strong></div>
      <div><span>Steps</span><strong>{steps.length}</strong></div>
      <div><span>Artifacts</span><strong>{artifacts.length}</strong></div>
      <div><span>Citations</span><strong>{citations.length}</strong></div>
    </div>

    <section className="run-section">
      <div className="run-section-heading"><div><span className="eyebrow">TRACE</span><h2>Execution timeline</h2></div><span>{steps.length} recorded steps</span></div>
      {steps.length ? <div className="step-timeline">{steps.map(step => <StepCard key={step.id} step={step} focused={focus?.stepId === step.id} />)}</div> : <InlineEmpty icon={<ListTree size={18} />} text="No execution steps were recorded." />}
    </section>

    <section className="run-section">
      <div className="run-section-heading"><div><span className="eyebrow">OUTPUTS</span><h2>Artifacts</h2></div><span>{artifacts.length} produced</span></div>
      {artifacts.length ? <div className="artifact-grid">{artifacts.map(artifact => <ArtifactCard key={artifact.id} artifact={artifact} citations={citations.filter(item => item.artifact_id === artifact.id)} focused={focus?.artifactId === artifact.id} />)}</div> : <InlineEmpty icon={<Boxes size={18} />} text="This run did not produce any artifacts." />}
    </section>

    <section className="run-section">
      <div className="run-section-heading"><div><span className="eyebrow">PROVENANCE</span><h2>Citations</h2></div><span>{citations.length} sources</span></div>
      {citations.length ? <div className="citation-list">{citations.map(citation => <CitationCard key={citation.id} citation={citation} />)}</div> : <InlineEmpty icon={<Link2 size={18} />} text="No citations were attached to this run." />}
    </section>
  </div>;
}

function StepCard({ step, focused = false }: { step: RunStep; focused?: boolean }) {
  const metadata = Object.entries(step.metadata || {});
  return <article className={`step-card ${focused ? "search-focus" : ""}`} id={`step-${step.id}`}>
    <div className={`step-node ${step.status}`}><StepIcon kind={step.kind} /></div>
    <div className="step-body">
      <div className="step-heading">
        <div><span className="step-sequence">STEP {step.sequence}</span><h3>{step.name}</h3></div>
        <div className="step-heading-meta"><span className="kind-chip">{step.kind}</span><StatusBadge status={step.status} compact /></div>
      </div>
      <div className="step-subline"><Clock3 size={12} />{formatDate(step.created_at, true)}{typeof step.metadata?.source === "string" && <span>via {step.metadata.source}</span>}</div>
      {metadata.length > 0 && <div className="metadata-strip">{metadata.slice(0, 8).map(([key, value]) => <span key={key}><b>{humanize(key)}</b>{metadataValue(value)}</span>)}</div>}
      {(step.input_content || step.output_content) && <div className="step-payloads">
        {step.input_content && <PayloadDetails label="Input" content={step.input_content} />}
        {step.output_content && <PayloadDetails label="Output" content={step.output_content} open={step.kind !== "model"} />}
      </div>}
    </div>
  </article>;
}

function ArtifactCard({ artifact, citations, focused = false }: { artifact: Artifact; citations: Citation[]; focused?: boolean }) {
  return <article className={`artifact-card ${focused ? "search-focus" : ""}`} id={`artifact-${artifact.id}`}>
    <div className="artifact-heading">
      <div className="artifact-icon"><ArtifactIcon kind={artifact.kind} /></div>
      <div><h3>{artifact.name}</h3><span>{artifact.media_type}</span></div>
      <span className="kind-chip">{artifact.kind}</span>
    </div>
    <div className="artifact-meta"><span>v{artifact.version}</span><span>{formatBytes(artifact.size_bytes)}</span><span>{citations.length} source{citations.length === 1 ? "" : "s"}</span></div>
    {artifact.uri && <UriValue uri={artifact.uri} />}
    {artifact.content_text && <PayloadDetails label="Preview" content={artifact.content_text} />}
  </article>;
}

function CitationCard({ citation }: { citation: Citation }) {
  const hasLocator = Object.keys(citation.locator || {}).length > 0;
  return <article className="citation-card">
    <div className="citation-icon"><Link2 size={16} /></div>
    <div className="citation-main">
      <div className="citation-heading"><strong>{citation.title}</strong><span className="kind-chip">{citation.source_kind}</span></div>
      {citation.source_id && <code>{citation.source_id}</code>}
      {citation.source_uri && <UriValue uri={citation.source_uri} />}
      {citation.excerpt && <p>{previewText(citation.excerpt, 800)}</p>}
      {hasLocator && <div className="locator">{Object.entries(citation.locator).map(([key, value]) => <span key={key}><b>{humanize(key)}</b>{metadataValue(value)}</span>)}</div>}
    </div>
  </article>;
}

function PayloadDetails({ label, content, open = false }: { label: string; content: string; open?: boolean }) {
  const preview = previewText(content, 20000);
  return <details className="payload" open={open}><summary>{label}<span>{content.length.toLocaleString()} chars</span></summary><pre>{preview}</pre>{preview.length < content.length && <small>Preview truncated in the UI.</small>}</details>;
}

function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  return <span className={`status-badge ${status} ${compact ? "compact" : ""}`}>{status.replace("_", " ")}</span>;
}

function StatusIcon({ status }: { status: RunStatus }) {
  if (status === "succeeded") return <CheckCircle2 className="status-icon succeeded" size={20} />;
  if (status === "running") return <LoaderCircle className="status-icon running spin" size={20} />;
  if (status === "cancelled") return <Ban className="status-icon cancelled" size={20} />;
  if (status === "limit_reached") return <Clock3 className="status-icon limit_reached" size={20} />;
  return <XCircle className="status-icon failed" size={20} />;
}

function StepIcon({ kind }: { kind: RunStep["kind"] }) {
  if (kind === "model") return <Cpu size={15} />;
  if (kind === "tool") return <Wrench size={15} />;
  if (kind === "retrieval") return <Database size={15} />;
  return <Bot size={15} />;
}

function ArtifactIcon({ kind }: { kind: string }) {
  if (kind === "json") return <Braces size={17} />;
  if (kind === "link") return <Link2 size={17} />;
  if (kind === "file") return <FileText size={17} />;
  return <FileText size={17} />;
}

function UriValue({ uri }: { uri: string }) {
  const external = /^https?:\/\//i.test(uri);
  return external
    ? <a className="uri-value" href={uri} target="_blank" rel="noreferrer"><ExternalLink size={12} />{uri}</a>
    : <code className="uri-value">{uri}</code>;
}

function InlineEmpty({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <div className="inline-empty">{icon}<span>{text}</span></div>;
}

function LoadingState({ label }: { label: string }) {
  return <div className="run-loading"><LoaderCircle className="spin" size={18} /><span>{label}</span></div>;
}

function formatDate(value?: string | null, includeSeconds = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: includeSeconds ? "2-digit" : undefined,
  }).format(date);
}

function formatDuration(start: string, end?: string | null) {
  if (!end) return "In progress";
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${Math.floor(milliseconds / 60000)}m ${Math.round((milliseconds % 60000) / 1000)}s`;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function previewText(value: string, limit: number) {
  return value.length > limit ? `${value.slice(0, limit)}\n…` : value;
}

function shortId(value: string) {
  return value ? value.slice(0, 8) : "—";
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}

function metadataValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function DecisionLab({ api, target, isAdmin, onOpenRun }: { api: Api; target: DecisionJump | null; isAdmin: boolean; onOpenRun: (runId: string, stepId?: string, artifactId?: string) => void }) {
  const [dashboard, setDashboard] = useState<DecisionDashboard | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(target?.caseId || null);
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("all");
  const [resolution, setResolution] = useState("all");
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [sourceName, setSourceName] = useState("NanoJev evaluation");
  const [threshold, setThreshold] = useState("0.7");
  const [requestFile, setRequestFile] = useState<File | null>(null);
  const [predictionsFile, setPredictionsFile] = useState<File | null>(null);
  const [metricsFile, setMetricsFile] = useState<File | null>(null);
  const [receiptFile, setReceiptFile] = useState<File | null>(null);

  const load = async (protocolId?: string, preferredCaseId?: string) => {
    setLoading(true);
    setError("");
    try {
      const suffix = protocolId ? `?protocol_id=${encodeURIComponent(protocolId)}` : "";
      const value = await api(`/api/decision-lab${suffix}`) as DecisionDashboard;
      setDashboard(value);
      const preferred = preferredCaseId || selectedId;
      setSelectedId(preferred && value.cases.some(item => item.id === preferred) ? preferred : value.cases[0]?.id || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load decision evidence");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(undefined, target?.caseId); }, [target?.key]);

  const importBundle = async (event: FormEvent) => {
    event.preventDefault();
    if (!requestFile || !predictionsFile || !metricsFile) return;
    setImporting(true);
    setError("");
    const body = new FormData();
    body.set("source_name", sourceName);
    body.set("confidence_threshold", threshold);
    body.set("request_file", requestFile);
    body.set("predictions_file", predictionsFile);
    body.set("metrics_file", metricsFile);
    if (receiptFile) body.set("receipt_file", receiptFile);
    try {
      const value = await api("/api/decision-lab/import/nanojev", { method: "POST", body }) as DecisionDashboard;
      setDashboard(value);
      setSelectedId(value.cases[0]?.id || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const cases = dashboard?.cases || [];
  const families = Object.keys(dashboard?.statistics.families || {});
  const needle = query.trim().toLocaleLowerCase();
  const visibleCases = cases.filter(item =>
    (family === "all" || item.family === family)
    && (resolution === "all" || item.resolution === resolution)
    && (!needle || `${item.source_case_id} ${item.state_text} ${item.family}`.toLocaleLowerCase().includes(needle))
  );
  const selected = cases.find(item => item.id === selectedId) || null;
  const protocol = dashboard?.protocol || null;
  const stats = dashboard?.statistics;

  return <section className="panel decision-lab">
    <header className="topbar decision-topbar">
      <div><span className="eyebrow">EVIDENCE CONTROL</span><h1>Decision Lab</h1><p className="topbar-copy">Inspect distributions, abstention, receipts, review scope, and frozen inputs.</p></div>
      <button className="secondary refresh-button" onClick={() => void load(protocol?.id)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />Refresh</button>
    </header>

    {isAdmin && <details className="decision-import">
      <summary><Upload size={15} /><span>Import a frozen NanoJev bundle</span><small>Upload only; the server never reads a host path.</small></summary>
      <form onSubmit={importBundle}>
        <label><span>Dataset name</span><input value={sourceName} onChange={event => setSourceName(event.target.value)} required /></label>
        <label><span>Review threshold</span><input type="number" min="0" max="1" step="0.01" value={threshold} onChange={event => setThreshold(event.target.value)} required /></label>
        <DecisionFile label="request.json" file={requestFile} required onChange={setRequestFile} />
        <DecisionFile label="predictions.json" file={predictionsFile} required onChange={setPredictionsFile} />
        <DecisionFile label="metrics.json" file={metricsFile} required onChange={setMetricsFile} />
        <DecisionFile label="receipt.yaml" file={receiptFile} onChange={setReceiptFile} />
        <button className="primary" type="submit" disabled={importing || !requestFile || !predictionsFile || !metricsFile}>{importing ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}{importing ? "Validating…" : "Validate & import"}</button>
      </form>
    </details>}

    {error && <div className="decision-error"><AlertCircle size={17} />{error}</div>}
    {loading && !dashboard && <LoadingState label="Loading decision evidence" />}
    {!loading && dashboard && !protocol && <div className="decision-empty"><Braces size={30} /><h2>No decision protocol yet</h2><p>Import a frozen NanoJev request, prediction, and metrics bundle to create the first evidence-backed view.</p></div>}
    {protocol && stats && <>
      <div className="decision-protocol-bar">
        <label><span>Protocol</span><select value={protocol.id} onChange={event => void load(event.target.value)}>{dashboard?.protocols.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <div><StatusBadge status={protocol.status} /><code>{shortId(protocol.content_hash)}</code><span>{formatDate(protocol.created_at)}</span></div>
      </div>
      <div className="decision-overview">
        <div><span>Cases</span><strong>{stats.cases}</strong></div>
        <div><span>Decided</span><strong>{stats.decided}</strong></div>
        <div><span>Manual review</span><strong>{stats.manual_review}</strong></div>
        <div><span>Mean confidence</span><strong>{formatPercent(stats.mean_confidence)}</strong></div>
        <div><span>Mean entropy</span><strong>{formatPercent(stats.mean_entropy)}</strong></div>
      </div>
      <div className="decision-workbench">
        <aside className="decision-index">
          <div className="decision-filter-search"><Search size={14} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter case or state" /></div>
          <div className="decision-filters"><select value={family} onChange={event => setFamily(event.target.value)}><option value="all">All families</option>{families.map(item => <option value={item} key={item}>{humanize(item)}</option>)}</select><select value={resolution} onChange={event => setResolution(event.target.value)}><option value="all">All outcomes</option><option value="decided">Decided</option><option value="manual_review">Manual review</option><option value="undetermined">Undetermined</option></select></div>
          <div className="decision-index-label"><span>{visibleCases.length} cases</span><span>threshold {formatPercent(selected?.threshold ?? 0.7)}</span></div>
          <div className="decision-case-list">{visibleCases.map(item => <button type="button" key={item.id} className={item.id === selectedId ? "decision-row active" : "decision-row"} onClick={() => setSelectedId(item.id)}><span className={`decision-state ${item.resolution}`} /> <span><strong>{item.source_case_id}</strong><small>{humanize(item.family)}</small></span><em>{formatPercent(item.confidence)}</em></button>)}</div>
        </aside>
        <main className="decision-detail-shell">
          {selected ? <DecisionCaseDetail decision={selected} protocol={protocol} onOpenRun={onOpenRun} /> : <div className="decision-empty"><Braces size={27} /><h2>Select a case</h2><p>Candidate distributions and evidence links will appear here.</p></div>}
        </main>
      </div>
    </>}
  </section>;
}

function DecisionFile({ label, file, required = false, onChange }: { label: string; file: File | null; required?: boolean; onChange: (file: File | null) => void }) {
  return <label className="decision-file"><span>{label}{required ? " *" : ""}</span><input type="file" accept={label.endsWith("json") ? "application/json,.json" : ".yaml,.yml,text/yaml"} required={required} onChange={event => onChange(event.target.files?.[0] || null)} /><small>{file ? `${file.name} · ${formatBytes(file.size)}` : "Not selected"}</small></label>;
}

function DecisionCaseDetail({ decision, protocol, onOpenRun }: { decision: DecisionCase; protocol: EvidenceProtocol; onOpenRun: (runId: string, stepId?: string, artifactId?: string) => void }) {
  const receipt = protocol.receipts.find(item => item.id === decision.receipt_id);
  return <div className="decision-detail">
    <div className="decision-detail-header">
      <div><span className="eyebrow">{humanize(decision.family)}</span><h2>{decision.source_case_id}</h2></div>
      <StatusBadge status={decision.resolution} />
    </div>
    <p className="decision-state-text">{decision.state_text}</p>
    <div className="decision-facts"><div><span>Confidence</span><strong>{formatPercent(decision.confidence)}</strong></div><div><span>Normalized entropy</span><strong>{formatPercent(decision.entropy)}</strong></div><div><span>Review threshold</span><strong>{formatPercent(decision.threshold)}</strong></div></div>
    {decision.resolution === "manual_review" && <div className="review-notice"><AlertCircle size={16} /><div><strong>Manual review required</strong><span>Confidence is below the frozen threshold. No external action is authorized.</span></div></div>}
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">QUESTIONS</span><h2>Candidate distributions</h2></div><span>{Object.keys(decision.questions).length} question(s)</span></div>
      <div className="decision-questions">
        {Object.entries(decision.questions).map(([questionId, question]) => <DecisionQuestion key={questionId} questionId={questionId} question={question} answer={decision.answers[questionId] || {}} />)}
      </div>
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">TRACEABILITY</span><h2>Execution links</h2></div><span>probability ≠ authority</span></div>
      {decision.run_id
        ? <button className="secondary" onClick={() => onOpenRun(decision.run_id!, decision.step_id || undefined, decision.primary_artifact_id || undefined)}><ListTree size={14} />Open linked run</button>
        : <InlineEmpty icon={<ListTree size={17} />} text="Imported fixture: no Agent Run was executed." />}
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">EVIDENCE</span><h2>Claim, receipt &amp; freeze</h2></div><span>{protocol.artifacts.length} artifacts</span></div>
      <div className="evidence-stack">
        {protocol.claims.map(claim => <article key={claim.id} className="evidence-card">
          <div><span className="kind-chip">claim · {claim.resolution}</span><code>{shortId(claim.id)}</code></div>
          <strong>{claim.statement}</strong><p>{claim.scope}</p>
          {claim.prohibited_upgrades.length > 0 && <details><summary>Prohibited upgrades</summary><ul>{claim.prohibited_upgrades.map(item => <li key={item}>{item}</li>)}</ul></details>}
        </article>)}
        {receipt && <article className="evidence-card">
          <div><span className="kind-chip">receipt · {receipt.status}</span><code>{shortId(receipt.id)}</code></div>
          <p>Input <code>{shortId(receipt.input_digest)}</code> · output <code>{shortId(receipt.output_digest)}</code></p>
          {receipt.reviews.map(review => <div className="review-row" key={review.id}><Shield size={14} /><span><strong>{review.independent ? "Independent" : "Local"} {humanize(review.reviewer_kind)} review</strong>{review.finding}</span></div>)}
        </article>}
        {protocol.freezes.map(freeze => <article className="evidence-card" key={freeze.id}>
          <div><span className="kind-chip">freeze v{freeze.version}</span><code>{shortId(freeze.content_hash)}</code></div>
          <strong>{freeze.name}</strong><p>{freeze.members.length} content-addressed members</p>
        </article>)}
      </div>
      <div className="decision-artifacts">
        {protocol.artifacts.map(artifact => <details key={artifact.id}>
          <summary><ArtifactIcon kind={artifact.kind} /><span>{artifact.name}<small>{formatBytes(artifact.size_bytes)} · {shortId(artifact.content_hash)}</small></span></summary>
          {artifact.preview && <pre>{previewText(artifact.preview, 4000)}</pre>}
        </details>)}
      </div>
    </section>
  </div>;
}

function DecisionQuestion({ questionId, question, answer }: { questionId: string; question: any; answer: any }) {
  const probabilities = Object.entries(answer.probabilities || {}) as [string, number][];
  const maximum = probabilities.reduce((value, [, probability]) => Math.max(value, Number(probability) || 0), 0);
  const criteria = question.criteria;
  const label = (key: string) => Array.isArray(criteria) ? `${key} · ${criteria[Number(key)] || key}` : `${key} · ${criteria?.[key] || key}`;
  return <article className="decision-question"><div className="decision-question-heading"><div><span className="kind-chip">{question.type}</span><h3>{question.instructions || questionId}</h3></div><code>{questionId}</code></div><div className="probability-list">{probabilities.sort((left, right) => right[1] - left[1]).map(([key, probability]) => <div className={probability === maximum ? "probability-row leading" : "probability-row"} key={key}><div><span>{label(key)}</span><strong>{formatPercent(probability)}</strong></div><div className="probability-track"><span style={{ width: `${Math.max(0, Math.min(1, probability)) * 100}%` }} /></div></div>)}</div><div className="decision-answer"><span>Projected value</span><strong>{metadataValue(answer.value ?? answer.choice ?? answer.level ?? "—")}</strong></div></article>;
}

function ResearchRegistry({ api, target, isAdmin, onOpenRun }: { api: Api; target: ResearchJump | null; isAdmin: boolean; onOpenRun: (runId: string, stepId?: string, artifactId?: string) => void }) {
  const [dashboard, setDashboard] = useState<ResearchDashboard | null>(null);
  const [claimDetail, setClaimDetail] = useState<ResearchClaim | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(target?.claimId || null);
  const [query, setQuery] = useState("");
  const [claimType, setClaimType] = useState("all");
  const [closure, setClosure] = useState("all");
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [sourceName, setSourceName] = useState("AI Frontier Claim Registry");
  const [registryFile, setRegistryFile] = useState<File | null>(null);
  const [ledgerFile, setLedgerFile] = useState<File | null>(null);

  const load = async (researchCaseId?: string, preferredClaimId?: string) => {
    setLoading(true);
    setError("");
    try {
      let caseId = researchCaseId;
      if (!caseId && preferredClaimId) {
        const claim = await api(`/api/research-registry/claims/${preferredClaimId}`) as ResearchClaim;
        caseId = claim.research_case_id;
      }
      const suffix = caseId ? `?research_case_id=${encodeURIComponent(caseId)}` : "";
      const value = await api(`/api/research-registry${suffix}`) as ResearchDashboard;
      setDashboard(value);
      const preferred = preferredClaimId || selectedId;
      setSelectedId(preferred && value.claims.some(item => item.id === preferred) ? preferred : value.claims[0]?.id || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load the research registry");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(undefined, target?.claimId); }, [target?.key]);

  const importRegistry = async (event: FormEvent) => {
    event.preventDefault();
    if (!registryFile) return;
    setImporting(true);
    setError("");
    const body = new FormData();
    body.set("source_name", sourceName);
    body.set("registry_file", registryFile);
    if (ledgerFile) body.set("source_ledger_file", ledgerFile);
    try {
      const value = await api("/api/research-registry/import/frontier", { method: "POST", body }) as ResearchDashboard;
      setDashboard(value);
      setSelectedId(value.claims[0]?.id || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const claims = dashboard?.claims || [];
  const types = Object.keys(dashboard?.statistics.claim_types || {});
  const needle = query.trim().toLocaleLowerCase();
  const visibleClaims = claims.filter(item =>
    (claimType === "all" || item.claim_type === claimType)
    && (closure === "all" || item.closure_status === closure)
    && (!needle || `${item.claim_key} ${item.statement} ${item.scope}`.toLocaleLowerCase().includes(needle))
  );
  const selected = visibleClaims.find(item => item.id === selectedId) || visibleClaims[0] || null;
  const researchCase = dashboard?.case || null;
  const protocol = dashboard?.protocol || null;
  const stats = dashboard?.statistics;

  useEffect(() => {
    if (!selected?.id) {
      setClaimDetail(null);
      return;
    }
    let current = true;
    void api(`/api/research-registry/claims/${selected.id}`)
      .then(value => { if (current) setClaimDetail(value as ResearchClaim); })
      .catch(reason => { if (current) setError(reason instanceof Error ? reason.message : "Unable to load claim details"); });
    return () => { current = false; };
  }, [selected?.id]);

  const refreshSelected = async () => {
    if (!researchCase || !selected) return;
    await load(researchCase.id, selected.id);
    setClaimDetail(await api(`/api/research-registry/claims/${selected.id}`) as ResearchClaim);
  };

  return <section className="panel decision-lab research-registry">
    <header className="topbar decision-topbar">
      <div><span className="eyebrow">VERIFIABLE RESEARCH</span><h1>Claim Explorer</h1><p className="topbar-copy">Inspect claim relations, verification receipts, promotion gates, and epistemic boundaries.</p></div>
      <button className="secondary refresh-button" onClick={() => void load(researchCase?.id)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />Refresh</button>
    </header>

    {isAdmin && <details className="decision-import research-import">
      <summary><Upload size={15} /><span>Import an AI Frontier Claim Registry</span><small>Structural validation only; no source is fetched automatically.</small></summary>
      <form onSubmit={importRegistry}>
        <label><span>Registry name</span><input value={sourceName} onChange={event => setSourceName(event.target.value)} required /></label>
        <ResearchFile label="claim-registry.json" accept="application/json,.json" file={registryFile} required onChange={setRegistryFile} />
        <ResearchFile label="public-source-ledger.md" accept="text/markdown,.md" file={ledgerFile} onChange={setLedgerFile} />
        <button className="primary" type="submit" disabled={importing || !registryFile}>{importing ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}{importing ? "Validating…" : "Validate & freeze"}</button>
      </form>
    </details>}

    {error && <div className="decision-error"><AlertCircle size={17} />{error}</div>}
    {loading && !dashboard && <LoadingState label="Loading research claims" />}
    {!loading && dashboard && !researchCase && <div className="decision-empty"><Microscope size={30} /><h2>No research registry yet</h2><p>Import a versioned AI Frontier Claim Registry to inspect claims and their declared source closure.</p></div>}
    {researchCase && protocol && stats && <>
      <div className="decision-protocol-bar">
        <label><span>Registry</span><select value={researchCase.id} onChange={event => void load(event.target.value)}>{dashboard?.cases.map(item => <option key={item.id} value={item.id}>{item.name} · {item.registry_version}</option>)}</select></label>
        <div><StatusBadge status={researchCase.status} /><code>{researchCase.registry_id}</code><span>{researchCase.as_of_date || formatDate(researchCase.created_at)}</span></div>
      </div>
      <div className="decision-overview research-overview">
        <div><span>Claims</span><strong>{stats.claims}</strong></div>
        <div><span>Closed refs</span><strong>{stats.closed}</strong></div>
        <div><span>Blocked</span><strong>{stats.blocked}</strong></div>
        <div><span>Unique sources</span><strong>{stats.sources}</strong></div>
        <div><span>Claim types</span><strong>{Object.keys(stats.claim_types).length}</strong></div>
        <div><span>Relations</span><strong>{dashboard?.relations.length || 0}</strong></div>
      </div>
      <div className="decision-workbench research-workbench">
        <aside className="decision-index">
          <div className="decision-filter-search"><Search size={14} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter claim or statement" /></div>
          <div className="decision-filters"><select value={claimType} onChange={event => setClaimType(event.target.value)}><option value="all">All claim types</option>{types.map(item => <option value={item} key={item}>{humanize(item)}</option>)}</select><select value={closure} onChange={event => setClosure(event.target.value)}><option value="all">All closure states</option><option value="closed">Closed refs</option><option value="blocked">Blocked</option></select></div>
          <div className="decision-index-label"><span>{visibleClaims.length} claims</span><span>revision-aware</span></div>
          <div className="decision-case-list research-claim-list">{visibleClaims.map(item => <button type="button" key={item.id} className={item.id === selected?.id ? "decision-row active" : "decision-row"} onClick={() => setSelectedId(item.id)}><span className={`decision-state ${item.closure_status}`} /><span><strong>{item.claim_key}</strong><small>{humanize(item.claim_type)} · {humanize(item.promotion_stage)}</small></span><em>r{item.revision_number}</em></button>)}</div>
        </aside>
        <main className="decision-detail-shell">
          {selected ? <ResearchClaimDetail key={selected.id} claim={claimDetail?.id === selected.id ? claimDetail : selected} claims={claims} researchCase={researchCase} protocol={protocol} api={api} isAdmin={isAdmin} onChanged={refreshSelected} onOpenRun={onOpenRun} /> : <div className="decision-empty"><GitBranch size={27} /><h2>Select a claim</h2><p>Relations, verification attempts, promotion gates, and source closure will appear here.</p></div>}
        </main>
      </div>
    </>}
  </section>;
}

function ResearchFile({ label, accept, file, required = false, onChange }: { label: string; accept: string; file: File | null; required?: boolean; onChange: (file: File | null) => void }) {
  return <label className="decision-file"><span>{label}{required ? " *" : ""}</span><input type="file" accept={accept} required={required} onChange={event => onChange(event.target.files?.[0] || null)} /><small>{file ? `${file.name} · ${formatBytes(file.size)}` : "Not selected"}</small></label>;
}

function ResearchClaimDetail({ claim, claims, researchCase, protocol, api, isAdmin, onChanged, onOpenRun }: { claim: ResearchClaim; claims: ResearchClaim[]; researchCase: ResearchCase; protocol: EvidenceProtocol; api: Api; isAdmin: boolean; onChanged: () => Promise<void>; onOpenRun: (runId: string, stepId?: string, artifactId?: string) => void }) {
  const receipt = protocol.receipts.find(item => item.id === researchCase.receipt_id);
  const relations = claim.relations || [];
  const attempts = claim.verification_attempts || [];
  const plans = claim.verification_plans || [];
  const executions = claim.verification_executions || [];
  const evaluations = claim.promotion_evaluations || [];
  const targets = claims.filter(item => item.id !== claim.id);
  const [relationTarget, setRelationTarget] = useState(targets[0]?.id || "");
  const [relationType, setRelationType] = useState<ResearchRelation["relation_type"]>("supports");
  const [rationale, setRationale] = useState("");
  const [attemptKind, setAttemptKind] = useState("source_audit");
  const [attemptOutcome, setAttemptOutcome] = useState("passed");
  const [method, setMethod] = useState("");
  const [attemptScope, setAttemptScope] = useState("");
  const [inputDigest, setInputDigest] = useState("");
  const [outputDigest, setOutputDigest] = useState("");
  const [artifactIds, setArtifactIds] = useState("");
  const [independent, setIndependent] = useState(false);
  const [planKey, setPlanKey] = useState("claim-check");
  const [planName, setPlanName] = useState("Claim verification");
  const [planKind, setPlanKind] = useState("source_audit");
  const [planMethod, setPlanMethod] = useState("Inspect the claim, its declared sources, and auditable tool results.");
  const [planScope, setPlanScope] = useState(claim.scope);
  const [planPrompt, setPlanPrompt] = useState("Verify this claim within its declared scope. Report contrary evidence and limitations explicitly.");
  const [planModel, setPlanModel] = useState("");
  const [autoPromote, setAutoPromote] = useState(true);
  const [working, setWorking] = useState(false);
  const [actionError, setActionError] = useState("");
  const nextStage = ({ registered: "evidence_ready", evidence_ready: "review_ready", review_ready: "release_ready" } as Record<string, string>)[claim.promotion_stage];

  const createRelation = async (event: FormEvent) => {
    event.preventDefault();
    setWorking(true); setActionError("");
    try {
      await api(`/api/research-registry/claims/${claim.id}/relations`, { method: "POST", body: JSON.stringify({ target_claim_id: relationTarget, relation_type: relationType, rationale }) });
      setRationale("");
      await onChanged();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to create relation"); }
    finally { setWorking(false); }
  };

  const recordAttempt = async (event: FormEvent) => {
    event.preventDefault();
    setWorking(true); setActionError("");
    try {
      await api(`/api/research-registry/claims/${claim.id}/verification-attempts`, { method: "POST", body: JSON.stringify({
        kind: attemptKind, outcome: attemptOutcome, method, scope: attemptScope,
        input_digest: inputDigest, output_digest: outputDigest, independent,
        artifact_ids: artifactIds.split(",").map(item => item.trim()).filter(Boolean),
      }) });
      setMethod(""); setAttemptScope(""); setInputDigest(""); setOutputDigest(""); setArtifactIds(""); setIndependent(false);
      await onChanged();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to record verification"); }
    finally { setWorking(false); }
  };

  const createPlan = async (event: FormEvent) => {
    event.preventDefault();
    setWorking(true); setActionError("");
    try {
      await api(`/api/research-registry/claims/${claim.id}/verification-plans`, { method: "POST", body: JSON.stringify({
        plan_key: planKey, name: planName, kind: planKind, method: planMethod, scope: planScope,
        prompt: planPrompt, model: planModel.trim() || null, auto_promote: autoPromote,
      }) });
      await onChanged();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to create verification plan"); }
    finally { setWorking(false); }
  };

  const runPlan = async (planId: string) => {
    setWorking(true); setActionError("");
    try {
      const result = await api(`/api/research-registry/verification-plans/${planId}/runs`, { method: "POST" });
      await onChanged();
      const runId = result?.execution?.run_id as string | undefined;
      if (runId) onOpenRun(runId, result?.step?.id, result?.artifact?.id);
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to execute verification plan"); }
    finally { setWorking(false); }
  };

  const withdrawRelation = async (relation: ResearchRelation) => {
    const reason = window.prompt("Why is this relation being withdrawn?");
    if (!reason?.trim()) return;
    setWorking(true); setActionError("");
    try {
      await api(`/api/research-registry/claims/${claim.id}/relations/${relation.id}/withdraw`, { method: "POST", body: JSON.stringify({ reason }) });
      await onChanged();
    } catch (error) { setActionError(error instanceof Error ? error.message : "Unable to withdraw relation"); }
    finally { setWorking(false); }
  };

  const evaluateGate = async () => {
    if (!nextStage) return;
    setWorking(true); setActionError("");
    try {
      await api(`/api/research-registry/claims/${claim.id}/promotion-gates`, { method: "POST", body: JSON.stringify({ target_stage: nextStage }) });
      await onChanged();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to evaluate promotion gate"); }
    finally { setWorking(false); }
  };

  return <div className="decision-detail research-detail">
    <div className="decision-detail-header">
      <div><span className="eyebrow">{humanize(claim.claim_type)} · revision {claim.revision_number}</span><h2>{claim.claim_key}</h2></div>
      <div className="research-status-pair"><StatusBadge status={claim.promotion_stage} /><StatusBadge status={claim.closure_status} /></div>
    </div>
    <p className="decision-state-text research-statement">{claim.statement}</p>
    <p className="research-scope"><strong>Declared scope</strong>{claim.scope}</p>
    <div className="decision-facts research-facts">
      <div><span>Uncertainty</span><strong>{humanize(claim.status_axes.uncertainty || "unknown")}</strong></div>
      <div><span>Causal status</span><strong>{humanize(claim.status_axes.causal || "unknown")}</strong></div>
      <div><span>Lifecycle</span><strong>{humanize(claim.lifecycle_status)}</strong></div>
      <div><span>Promotion</span><strong>{humanize(claim.promotion_stage)}</strong></div>
      <div><span>Method revision</span><strong>{claim.method_revision}</strong></div>
    </div>
    {claim.blockers.length > 0 && <div className="review-notice blocker-notice"><Ban size={16} /><div><strong>Promotion blocked</strong><span>{claim.blockers.map(humanize).join(" · ")}</span></div></div>}
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">SOURCE CLOSURE</span><h2>Declared source references</h2></div><span>{claim.sources.length} source(s)</span></div>
      {claim.sources.length ? <div className="research-source-list">{claim.sources.map(source => <article className="research-source" key={source.id}>
        <div><span className="kind-chip">{source.status}</span><strong>{source.source_key}</strong></div>
        <p>{source.locator}</p>
        <code title={source.content_hash}>{source.content_hash}</code>
      </article>)}</div> : <InlineEmpty icon={<Ban size={17} />} text="No source reference is declared for this revision." />}
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">CLAIM GRAPH</span><h2>Typed relations</h2></div><span>{relations.length} edge(s)</span></div>
      {relations.length ? <div className="research-relation-list">{relations.map(relation => {
        const outgoing = relation.source_claim_id === claim.id;
        return <article className={`research-relation ${relation.relation_type}`} key={relation.id}><GitBranch size={16} /><div><span className="kind-chip">{humanize(relation.relation_type)}</span><strong>{outgoing ? `→ ${relation.target_claim_key}` : `← ${relation.source_claim_key}`}</strong><p>{relation.rationale}</p>{relation.withdrawal_reason && <small>Withdrawn: {relation.withdrawal_reason}</small>}</div><div className="research-relation-actions"><StatusBadge status={relation.status} />{isAdmin && relation.status === "active" && <button className="icon-button" type="button" title="Withdraw relation" aria-label="Withdraw relation" disabled={working} onClick={() => void withdrawRelation(relation)}><Ban size={13} /></button>}</div></article>;
      })}</div> : <InlineEmpty icon={<GitBranch size={17} />} text="No typed relation is registered for this revision." />}
      {isAdmin && targets.length > 0 && <details className="research-action"><summary><Plus size={15} />Register relation</summary><form onSubmit={createRelation}><div className="research-action-grid"><label><span>Relation</span><select value={relationType} onChange={event => setRelationType(event.target.value as ResearchRelation["relation_type"])}><option value="supports">Supports</option><option value="refutes">Refutes</option><option value="depends_on">Depends on</option><option value="qualifies">Qualifies</option></select></label><label><span>Target claim</span><select value={relationTarget} onChange={event => setRelationTarget(event.target.value)}>{targets.map(item => <option value={item.id} key={item.id}>{item.claim_key}</option>)}</select></label></div><label><span>Rationale</span><textarea rows={3} value={rationale} onChange={event => setRationale(event.target.value)} required /></label><button className="secondary" type="submit" disabled={working || !relationTarget || !rationale.trim()}><GitBranch size={14} />Save typed edge</button></form></details>}
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">VERIFICATION RUNNER</span><h2>Versioned Agent plans</h2></div><span>{plans.filter(plan => plan.status === "active").length} active · {executions.length} run(s)</span></div>
      {plans.length ? <div className="research-plan-list">{plans.map(plan => <article className="research-plan" key={plan.id}>
        <div className="research-plan-heading"><Activity size={16} /><span><strong>{plan.name}</strong><small>{plan.plan_key} · v{plan.version} · {humanize(plan.kind)}</small></span><StatusBadge status={plan.status} /></div>
        <p>{plan.method}</p><small>{plan.scope}</small>
        <div className="research-plan-footer"><code>{plan.result_contract_version} · {shortId(plan.content_digest)}</code>{isAdmin && plan.status === "active" && <button className="secondary" type="button" disabled={working} onClick={() => void runPlan(plan.id)}>{working ? <LoaderCircle className="spin" size={13} /> : <Activity size={13} />}Run Agent</button>}</div>
      </article>)}</div> : <InlineEmpty icon={<Activity size={17} />} text="No executable verification plan has been frozen." />}
      {executions.length > 0 && <div className="research-execution-list">{executions.map(execution => <article className="research-execution" key={execution.id}><div><StatusBadge status={execution.status} /><strong>Plan v{execution.plan_version}</strong><small>{formatDate(execution.started_at)}</small></div><div><span>{execution.outcome ? humanize(execution.outcome) : "No scientific outcome"}</span>{execution.error_code && <code>{execution.error_code}</code>}{execution.run_id && <button className="text-button" type="button" onClick={() => onOpenRun(execution.run_id!, undefined, execution.artifact_id || undefined)}>Open Run <ArrowRight size={12} /></button>}</div></article>)}</div>}
      {isAdmin && <details className="research-action"><summary><Plus size={15} />Create next plan version</summary><form onSubmit={createPlan}><div className="research-action-grid"><label><span>Stable plan key</span><input pattern="[a-z][a-z0-9_.-]*" value={planKey} onChange={event => setPlanKey(event.target.value)} required /></label><label><span>Name</span><input value={planName} onChange={event => setPlanName(event.target.value)} required /></label></div><div className="research-action-grid"><label><span>Kind</span><select value={planKind} onChange={event => setPlanKind(event.target.value)}><option value="source_audit">Source audit</option><option value="reproduction">Reproduction</option><option value="calculation">Calculation</option><option value="experiment">Experiment</option><option value="review">Review</option></select></label><label><span>Model <small>optional</small></span><input value={planModel} onChange={event => setPlanModel(event.target.value)} placeholder="Workspace default" /></label></div><label><span>Method</span><textarea rows={3} value={planMethod} onChange={event => setPlanMethod(event.target.value)} required /></label><label><span>Scope</span><textarea rows={2} value={planScope} onChange={event => setPlanScope(event.target.value)} required /></label><label><span>Agent instructions</span><textarea rows={5} value={planPrompt} onChange={event => setPlanPrompt(event.target.value)} required /></label><label className="research-check"><input type="checkbox" checked={autoPromote} onChange={event => setAutoPromote(event.target.checked)} /><span>Evaluate the next Promotion Gate after every valid or error receipt</span></label><button className="secondary" type="submit" disabled={working || !planKey.trim() || !planName.trim() || !planMethod.trim() || !planScope.trim() || !planPrompt.trim()}><Activity size={14} />Freeze plan version</button><small>For recurring execution, schedule target <code>research.verify</code> with payload <code>{`{"plan_id":"…"}`}</code>. Creating a new version retires the previous active version.</small></form></details>}
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">VERIFICATION</span><h2>Receipt-bound attempts</h2></div><span>{attempts.length} attempt(s)</span></div>
      {attempts.length ? <div className="research-attempt-list">{attempts.map(attempt => <article className="research-attempt" key={attempt.id}><div><Beaker size={16} /><span><strong>{humanize(attempt.kind)}</strong><small>{attempt.independent ? "Independent" : "Local"} · {formatDate(attempt.created_at)}</small></span><StatusBadge status={attempt.outcome} /></div><p>{attempt.method}</p><small>{attempt.scope}</small><code>receipt {shortId(attempt.receipt_id)} · {attempt.artifact_ids.length} artifact(s)</code></article>)}</div> : <InlineEmpty icon={<Beaker size={17} />} text="No verification attempt has been recorded." />}
      {isAdmin && <details className="research-action"><summary><Plus size={15} />Record verification attempt</summary><form onSubmit={recordAttempt}><div className="research-action-grid"><label><span>Kind</span><select value={attemptKind} onChange={event => setAttemptKind(event.target.value)}><option value="source_audit">Source audit</option><option value="reproduction">Reproduction</option><option value="calculation">Calculation</option><option value="experiment">Experiment</option><option value="review">Review</option></select></label><label><span>Outcome</span><select value={attemptOutcome} onChange={event => setAttemptOutcome(event.target.value)}><option value="passed">Passed</option><option value="failed">Failed</option><option value="inconclusive">Inconclusive</option><option value="error">Error</option></select></label></div><label><span>Method</span><textarea rows={3} value={method} onChange={event => setMethod(event.target.value)} required /></label><label><span>Scope</span><textarea rows={2} value={attemptScope} onChange={event => setAttemptScope(event.target.value)} required /></label><div className="research-action-grid"><label><span>Input SHA-256</span><input pattern="[0-9a-f]{64}" value={inputDigest} onChange={event => setInputDigest(event.target.value)} required /></label><label><span>Output SHA-256</span><input pattern="[0-9a-f]{64}" value={outputDigest} onChange={event => setOutputDigest(event.target.value)} required /></label></div><label><span>Artifact IDs <small>comma-separated</small></span><input value={artifactIds} onChange={event => setArtifactIds(event.target.value)} /></label><label className="research-check"><input type="checkbox" checked={independent} onChange={event => setIndependent(event.target.checked)} /><span>Declare independent performance/review (self-attested)</span></label><button className="secondary" type="submit" disabled={working || !method.trim() || !attemptScope.trim() || inputDigest.length !== 64 || outputDigest.length !== 64}><Beaker size={14} />Bind receipt</button></form></details>}
    </section>
    <section className="decision-section promotion-section">
      <div className="run-section-heading"><div><span className="eyebrow">PROMOTION CONTROL</span><h2>Gate evaluations</h2></div><span>{evaluations.length} immutable record(s)</span></div>
      <div className="promotion-path"><span className={claim.promotion_stage === "registered" ? "active" : "complete"}>Registered</span><ChevronRight size={14} /><span className={claim.promotion_stage === "evidence_ready" ? "active" : ["review_ready", "release_ready"].includes(claim.promotion_stage) ? "complete" : ""}>Evidence ready</span><ChevronRight size={14} /><span className={claim.promotion_stage === "review_ready" ? "active" : claim.promotion_stage === "release_ready" ? "complete" : ""}>Review ready</span><ChevronRight size={14} /><span className={claim.promotion_stage === "release_ready" ? "active" : ""}>Release ready</span></div>
      {evaluations.length ? <div className="promotion-history">{[...evaluations].reverse().map(evaluation => <details key={evaluation.id} open={evaluation.id === evaluations[evaluations.length - 1]?.id}><summary><StatusBadge status={evaluation.decision} /><strong>{humanize(evaluation.from_stage)} → {humanize(evaluation.target_stage)}</strong><small>{formatDate(evaluation.created_at)}</small></summary><div className="promotion-criteria">{evaluation.criteria.map(criterion => <div className={criterion.passed ? "passed" : "blocked"} key={criterion.code}>{criterion.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}<span><strong>{humanize(criterion.code)}</strong><small>{metadataValue(criterion.observed)}</small></span></div>)}</div><code>{evaluation.policy_version} · {shortId(evaluation.evaluation_digest)}</code></details>)}</div> : <InlineEmpty icon={<Shield size={17} />} text="No promotion gate has been evaluated." />}
      {isAdmin && nextStage && <div className="promotion-action"><div><strong>Evaluate {humanize(nextStage)}</strong><span>The gate recomputes current source closure, verification receipts, refutations, and dependencies. A blocked evaluation is retained without changing the stage.</span></div><button className="primary" type="button" onClick={() => void evaluateGate()} disabled={working}><Shield size={14} />Evaluate gate</button></div>}
      {actionError && <div className="decision-error"><AlertCircle size={15} />{actionError}</div>}
    </section>
    <section className="decision-section">
      <div className="run-section-heading"><div><span className="eyebrow">CONTROL PLANE</span><h2>Receipt, review &amp; freeze</h2></div><span>closure ≠ validity</span></div>
      <div className="evidence-stack">
        {receipt && <article className="evidence-card"><div><span className="kind-chip">receipt · {receipt.status}</span><code>{shortId(receipt.id)}</code></div><p>Input <code>{shortId(receipt.input_digest)}</code> · normalized output <code>{shortId(receipt.output_digest)}</code></p>{receipt.reviews.map(review => <div className="review-row" key={review.id}><Shield size={14} /><span><strong>{review.independent ? "Independent" : "Local"} {humanize(review.reviewer_kind)} review</strong>{review.finding}</span></div>)}</article>}
        {protocol.freezes.map(freeze => <article className="evidence-card" key={freeze.id}><div><span className="kind-chip">freeze v{freeze.version}</span><code>{shortId(freeze.content_hash)}</code></div><strong>{freeze.name}</strong><p>{freeze.members.length} content-addressed member(s). This freezes representation, not scientific truth.</p></article>)}
      </div>
      <div className="decision-artifacts">{protocol.artifacts.map(artifact => <details key={artifact.id}><summary><ArtifactIcon kind={artifact.kind} /><span>{artifact.name}<small>{formatBytes(artifact.size_bytes)} · {shortId(artifact.content_hash)}</small></span></summary>{artifact.preview && <pre>{previewText(artifact.preview, 4000)}</pre>}</details>)}</div>
    </section>
  </div>;
}

function formatPercent(value: number) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function Knowledge({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [query, setQuery] = useState(""); const [results, setResults] = useState<any[]>([]); const [name, setName] = useState(""); const [content, setContent] = useState(""); const search = async () => setResults(await api(`/api/knowledge/search?q=${encodeURIComponent(query)}`)); const save = async () => { await api("/api/knowledge/documents", { method: "POST", body: JSON.stringify({ name, content }) }); setName(""); setContent(""); await search(); }; return <section className="panel"><header className="topbar"><div><span className="eyebrow">KNOWLEDGE</span><h1>Workspace documents</h1></div></header><div className="split"><div className="form-card"><h2>Add a document</h2><input placeholder="Document name" value={name} onChange={e => setName(e.target.value)} /><textarea placeholder="Paste text or Markdown" rows={10} value={content} onChange={e => setContent(e.target.value)} /><button className="primary" onClick={() => void save()} disabled={!name || !content}><Upload size={15} />Save document</button></div><div className="form-card"><h2>Search knowledge</h2><div className="search-row"><input placeholder="Search your workspace" value={query} onChange={e => setQuery(e.target.value)} /><button className="icon-button" onClick={() => void search()} aria-label="Search"><Search size={16} /></button></div><div className="result-list">{results.map(item => <article className="result" key={item.id}><strong>{item.name}</strong><span>{item.content}</span></article>)}</div></div></div></section>; }

function Models({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [models, setModels] = useState<any[]>([]); const [name, setName] = useState(""); const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1"); const [model, setModel] = useState(""); useEffect(() => { void api("/api/models").then(setModels); }, []); const save = async () => { const value = await api("/api/models", { method: "POST", body: JSON.stringify({ name, base_url: baseUrl, model, is_default: models.length === 0 }) }); setModels([...models, value]); setName(""); setModel(""); }; return <section className="panel"><header className="topbar"><div><span className="eyebrow">CONFIGURATION</span><h1>Model providers</h1></div></header><div className="split"><div className="form-card"><h2>Connect a provider</h2><input placeholder="Provider name" value={name} onChange={e => setName(e.target.value)} /><input placeholder="OpenAI-compatible base URL" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} /><input placeholder="Model name" value={model} onChange={e => setModel(e.target.value)} /><button className="primary" onClick={() => void save()} disabled={!name || !model}><Settings size={15} />Save provider</button></div><div className="form-card"><h2>Configured models</h2>{models.map(item => <article className="model-row" key={item.id}><div><strong>{item.name}</strong><span>{item.model}</span></div>{item.is_default ? <em>Default</em> : null}</article>)}</div></div></section>; }

function Admin({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [tenants, setTenants] = useState<any[]>([]); const [usage, setUsage] = useState<any>({}); useEffect(() => { void Promise.all([api("/api/admin/tenants"), api("/api/usage")]).then(([nextTenants, nextUsage]) => { setTenants(nextTenants); setUsage(nextUsage); }); }, []); return <section className="panel"><header className="topbar"><div><span className="eyebrow">ADMINISTRATION</span><h1>Operations overview</h1></div></header><div className="metric-grid"><div className="metric"><span>Requests</span><strong>{usage.requests || 0}</strong></div><div className="metric"><span>Input tokens</span><strong>{usage.prompt_tokens || 0}</strong></div><div className="metric"><span>Estimated cost</span><strong>{Number(usage.cost || 0).toFixed(4)}</strong></div><div className="metric"><span>Tenants</span><strong>{tenants.length}</strong></div></div><div className="form-card"><h2><Users size={16} />Tenants</h2>{tenants.map(item => <article className="model-row" key={item.id}><div><strong>{item.name}</strong><span>{item.id}</span></div><em>Active</em></article>)}</div></section>; }
