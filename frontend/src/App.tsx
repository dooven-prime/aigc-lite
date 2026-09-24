import { FormEvent, useEffect, useState } from "react";
import { BookOpen, Bot, LogIn, LogOut, Plus, Search, Send, Settings, Shield, Upload, Users } from "lucide-react";

type Session = { id: string; title: string; messages?: Message[] };
type Message = { role: string; content: string };
type User = { id: string; tenant_id: string; email: string; name: string; role: string };
type Panel = "chat" | "knowledge" | "admin" | "models";

const storedToken = localStorage.getItem("aigc-lite-token") || "";

export function App() {
  const [token, setToken] = useState(storedToken);
  const [user, setUser] = useState<User | null>(null);
  const [panel, setPanel] = useState<Panel>("chat");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(false);

  const api = async (path: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
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
      <div className="nav-group"><button className={panel === "chat" ? "nav active" : "nav"} onClick={() => setPanel("chat")}><Bot size={16} />Workspace</button><button className={panel === "knowledge" ? "nav active" : "nav"} onClick={() => setPanel("knowledge")}><BookOpen size={16} />Knowledge</button><button className={panel === "models" ? "nav active" : "nav"} onClick={() => setPanel("models")}><Settings size={16} />Models</button>{user.role === "admin" && <button className={panel === "admin" ? "nav active" : "nav"} onClick={() => setPanel("admin")}><Shield size={16} />Administration</button>}</div>
      {panel === "chat" && <><div className="section-label">Recent conversations</div><div className="session-list">{sessions.map(item => <button className={item.id === session?.id ? "session active" : "session"} key={item.id} onClick={() => void openSession(item.id)}>{item.title}</button>)}</div></>}
      <button className="logout" onClick={logout}><LogOut size={15} />Sign out</button>
    </nav>
    <main className="content">{panel === "chat" && <Chat session={session} loading={loading} onSend={send} onCreate={createSession} />}{panel === "knowledge" && <Knowledge api={api} />}{panel === "models" && <Models api={api} />}{panel === "admin" && <Admin api={api} />}</main>
  </div>;
}

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [register, setRegister] = useState(false); const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [name, setName] = useState(""); const [workspace, setWorkspace] = useState(""); const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setError(""); try { const response = await fetch(`/api/auth/${register ? "register" : "login"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(register ? { email, password, name, workspace_name: workspace } : { email, password }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail); onLogin(data.access_token); } catch (e) { setError(e instanceof Error ? e.message : "Unable to sign in"); } };
  return <div className="auth-page"><form className="auth-card" onSubmit={submit}><div className="brand centered"><div className="brand-mark"><Bot size={20} /></div><div><strong>aigc-lite</strong><small>AI workspace</small></div></div><h1>{register ? "Create your workspace" : "Welcome back"}</h1>{register && <input placeholder="Workspace name" value={name} onChange={e => setName(e.target.value)} required />}{register && <input placeholder="Your name" value={name} onChange={e => setName(e.target.value)} required />}<input type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} required /><input type="password" placeholder="Password, 8+ characters" minLength={8} value={password} onChange={e => setPassword(e.target.value)} required />{error && <div className="error">{error}</div>}<button className="primary" type="submit"><LogIn size={16} />{register ? "Create account" : "Sign in"}</button><button className="text-button" type="button" onClick={() => setRegister(!register)}>{register ? "Already have an account? Sign in" : "Create a new workspace"}</button></form></div>;
}

function Chat({ session, loading, onSend, onCreate }: { session: Session | null; loading: boolean; onSend: (text: string) => Promise<void>; onCreate: () => Promise<void> }) { const [prompt, setPrompt] = useState(""); const submit = async (event: FormEvent) => { event.preventDefault(); const next = prompt; setPrompt(""); await onSend(next); }; return <div className="chat-view"><header className="topbar"><div><span className="eyebrow">WORKSPACE</span><h1>{session?.title || "Start a conversation"}</h1></div><span className="status-dot">Ready</span></header><div className="messages">{!session && <div className="empty"><div className="empty-icon"><Bot size={24} /></div><h2>What are you working on?</h2><p>Ask a question, analyze a document, or connect a tool.</p><button className="secondary" onClick={onCreate}><Plus size={15} />New conversation</button></div>}{session?.messages?.map((message, index) => <div className={message.role === "user" ? "message user" : "message assistant"} key={`${index}-${message.role}`}><div className="message-label">{message.role === "user" ? "You" : "aigc-lite"}</div><div>{message.content}</div></div>)}{loading && <div className="message assistant"><div className="message-label">aigc-lite</div><div className="typing">Thinking...</div></div>}</div><form className="composer" onSubmit={submit}><textarea value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Ask anything..." rows={2} /><button className="send" disabled={loading || !prompt.trim()} aria-label="Send"><Send size={17} /></button></form></div>; }

function Knowledge({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [query, setQuery] = useState(""); const [results, setResults] = useState<any[]>([]); const [name, setName] = useState(""); const [content, setContent] = useState(""); const search = async () => setResults(await api(`/api/knowledge/search?q=${encodeURIComponent(query)}`)); const save = async () => { await api("/api/knowledge/documents", { method: "POST", body: JSON.stringify({ name, content }) }); setName(""); setContent(""); await search(); }; return <section className="panel"><header className="topbar"><div><span className="eyebrow">KNOWLEDGE</span><h1>Workspace documents</h1></div></header><div className="split"><div className="form-card"><h2>Add a document</h2><input placeholder="Document name" value={name} onChange={e => setName(e.target.value)} /><textarea placeholder="Paste text or Markdown" rows={10} value={content} onChange={e => setContent(e.target.value)} /><button className="primary" onClick={() => void save()} disabled={!name || !content}><Upload size={15} />Save document</button></div><div className="form-card"><h2>Search knowledge</h2><div className="search-row"><input placeholder="Search your workspace" value={query} onChange={e => setQuery(e.target.value)} /><button className="icon-button" onClick={() => void search()} aria-label="Search"><Search size={16} /></button></div><div className="result-list">{results.map(item => <article className="result" key={item.id}><strong>{item.name}</strong><span>{item.content}</span></article>)}</div></div></div></section>; }

function Models({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [models, setModels] = useState<any[]>([]); const [name, setName] = useState(""); const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1"); const [model, setModel] = useState(""); useEffect(() => { void api("/api/models").then(setModels); }, []); const save = async () => { const value = await api("/api/models", { method: "POST", body: JSON.stringify({ name, base_url: baseUrl, model, is_default: models.length === 0 }) }); setModels([...models, value]); setName(""); setModel(""); }; return <section className="panel"><header className="topbar"><div><span className="eyebrow">CONFIGURATION</span><h1>Model providers</h1></div></header><div className="split"><div className="form-card"><h2>Connect a provider</h2><input placeholder="Provider name" value={name} onChange={e => setName(e.target.value)} /><input placeholder="OpenAI-compatible base URL" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} /><input placeholder="Model name" value={model} onChange={e => setModel(e.target.value)} /><button className="primary" onClick={() => void save()} disabled={!name || !model}><Settings size={15} />Save provider</button></div><div className="form-card"><h2>Configured models</h2>{models.map(item => <article className="model-row" key={item.id}><div><strong>{item.name}</strong><span>{item.model}</span></div>{item.is_default ? <em>Default</em> : null}</article>)}</div></div></section>; }

function Admin({ api }: { api: (path: string, init?: RequestInit) => Promise<any> }) { const [tenants, setTenants] = useState<any[]>([]); const [usage, setUsage] = useState<any>({}); useEffect(() => { void Promise.all([api("/api/admin/tenants"), api("/api/usage")]).then(([nextTenants, nextUsage]) => { setTenants(nextTenants); setUsage(nextUsage); }); }, []); return <section className="panel"><header className="topbar"><div><span className="eyebrow">ADMINISTRATION</span><h1>Operations overview</h1></div></header><div className="metric-grid"><div className="metric"><span>Requests</span><strong>{usage.requests || 0}</strong></div><div className="metric"><span>Input tokens</span><strong>{usage.prompt_tokens || 0}</strong></div><div className="metric"><span>Estimated cost</span><strong>{Number(usage.cost || 0).toFixed(4)}</strong></div><div className="metric"><span>Tenants</span><strong>{tenants.length}</strong></div></div><div className="form-card"><h2><Users size={16} />Tenants</h2>{tenants.map(item => <article className="model-row" key={item.id}><div><strong>{item.name}</strong><span>{item.id}</span></div><em>Active</em></article>)}</div></section>; }
