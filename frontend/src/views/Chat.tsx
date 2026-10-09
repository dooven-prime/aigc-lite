import { FormEvent, useEffect, useRef, useState } from "react";
import { Bot, FileText, Plus, Send, Square, X } from "lucide-react";
import { consumeChatStream } from "./chatStream";

type Message = { role: string; content: string };
type Session = { id: string; title: string; messages?: Message[] };
type Attachment = { name: string; content: string; size: number };
type PendingTurn = { user: string; assistant: string };

const MAX_FILE_BYTES = 16_384;
const MAX_TOTAL_BYTES = 32_768;

function errorText(value: unknown): string {
  return value instanceof Error ? value.message : "Request failed";
}

export function Chat({ session, token, onCommitted }: {
  session: Session | null;
  token: string;
  onCommitted: (sessionId: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const runRef = useRef<string | null>(null);
  const cancelRequestedRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages?.length, pending?.assistant, pending?.user]);

  useEffect(() => () => {
    if (runRef.current) {
      void fetch(`/api/runs/${runRef.current}/cancel`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      }).catch(() => undefined);
    }
    controllerRef.current?.abort();
  }, [token]);

  const addFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    try {
      if (attachments.length + files.length > 4) throw new Error("At most four files per message");
      const next: Attachment[] = [];
      for (const file of Array.from(files)) {
        if (!/\.(txt|md)$/i.test(file.name) || file.name.includes("/") || file.name.includes("\\")) {
          throw new Error("Only .txt and .md files are supported");
        }
        if (!file.size || file.size > MAX_FILE_BYTES) throw new Error("Each file must be 1–16 KiB");
        const content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
        if (!content.trim() || content.includes("\0")) throw new Error("Files must contain valid UTF-8 text");
        next.push({ name: file.name, content, size: file.size });
      }
      if ([...attachments, ...next].reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL_BYTES) {
        throw new Error("Combined attachments must be at most 32 KiB");
      }
      setAttachments(current => [...current, ...next]);
      setError("");
    } catch (cause) { setError(errorText(cause)); }
    finally { if (inputRef.current) inputRef.current.value = ""; }
  };

  const cancel = async () => {
    cancelRequestedRef.current = true;
    setCancelling(true);
    try {
      if (runRef.current) {
        await fetch(`/api/runs/${runRef.current}/cancel`, {
          method: "POST", headers: { Authorization: `Bearer ${token}` },
        });
      }
    } catch { /* The connection may already be closing. */ }
    finally { controllerRef.current?.abort(); }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const prompt = draft.trim();
    if (busy || !prompt) return;
    const selected = attachments;
    const displayed = prompt + (selected.length ? `\n\n[Attached files: ${selected.map(file => file.name).join(", ")}]` : "");
    setPending({ user: displayed, assistant: "" });
    setDraft("");
    setAttachments([]);
    setError("");
    setBusy(true);
    setCancelling(false);
    cancelRequestedRef.current = false;
    const controller = new AbortController();
    controllerRef.current = controller;
    let acceptedSessionId: string | null = null;
    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt, session_id: session?.id ?? null,
          attachments: selected.map(({ name, content }) => ({ name, content })),
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
      }
      acceptedSessionId = response.headers.get("X-Session-Id");
      runRef.current = response.headers.get("X-Run-Id");
      await consumeChatStream(response, content => {
        setPending(current => current && { ...current, assistant: current.assistant + content });
      });
      if (!acceptedSessionId) throw new Error("Server did not identify the conversation");
      await onCommitted(acceptedSessionId);
      setPending(null);
    } catch (cause) {
      setError(cancelRequestedRef.current ? "Run cancelled. You can edit and resend the draft." : errorText(cause));
      setDraft(prompt);
      setAttachments(selected);
      if (acceptedSessionId) {
        try { await onCommitted(acceptedSessionId); setPending(null); }
        catch { /* Keep the local turn visible if history cannot be refreshed. */ }
      } else {
        setPending(null);
      }
    } finally {
      runRef.current = null;
      controllerRef.current = null;
      setBusy(false);
      setCancelling(false);
    }
  };

  return <div className="chat-view">
    <header className="topbar"><div><span className="eyebrow">WORKSPACE</span><h1>{session?.title || "Start a conversation"}</h1></div><span className="status-dot">Model stream · no tools</span></header>
    <div className="messages">
      {!session && !pending && <div className="empty"><div className="empty-icon"><Bot size={24} /></div><h2>What are you working on?</h2><p>Send a message to start a conversation. Text attachments stay with this Run, not the knowledge index.</p></div>}
      {session?.messages?.map((message, index) => <div className={message.role === "user" ? "message user" : "message assistant"} key={`${index}-${message.role}`}><div className="message-label">{message.role === "user" ? "You" : "aigc-lite"}</div><div>{message.content}</div></div>)}
      {pending && <><div className="message user"><div className="message-label">You</div><div>{pending.user}</div></div><div className="message assistant" aria-live="polite"><div className="message-label">aigc-lite · {pending.assistant ? "Streaming" : "Thinking"}</div><div>{pending.assistant || "…"}</div></div></>}
      <div ref={bottomRef} />
    </div>
    <div className="chat-composer-wrap">
      {error && <div className="chat-error" role="alert">{error} <span>Your draft has been restored; resending starts a new Run.</span></div>}
      {attachments.length > 0 && <div className="chat-attachments">{attachments.map((file, index) => <span className="chat-attachment" key={`${file.name}-${index}`}><FileText size={14} />{file.name}<button type="button" aria-label={`Remove ${file.name}`} onClick={() => setAttachments(current => current.filter((_, i) => i !== index))}><X size={13} /></button></span>)}</div>}
      <form className="composer" onSubmit={submit}>
        <input ref={inputRef} type="file" accept=".txt,.md,text/plain,text/markdown" multiple hidden onChange={event => void addFiles(event.target.files)} />
        <button className="secondary chat-add-file" type="button" disabled={busy} onClick={() => inputRef.current?.click()}><Plus size={16} />File</button>
        <textarea value={draft} onChange={event => setDraft(event.target.value)} placeholder="Ask anything…" rows={2} disabled={busy} />
        {busy ? <button className="chat-stop" type="button" disabled={cancelling} onClick={() => void cancel()}><Square size={15} />{cancelling ? "Stopping" : "Stop"}</button> : <button className="send" disabled={!draft.trim()} aria-label="Send"><Send size={17} /></button>}
      </form>
    </div>
  </div>;
}
