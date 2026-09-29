import { useEffect, useState } from "react";
import { Settings } from "lucide-react";

type Api = (path: string, init?: RequestInit) => Promise<any>;

type ModelConfig = {
  id: string;
  name: string;
  model: string;
  is_default: boolean;
};

type Credential = {
  id: string;
  name: string;
  reference: string;
  configured: boolean;
};

export function Models({ api, isAdmin }: { api: Api; isAdmin: boolean }) {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [model, setModel] = useState("");
  const [credentialReference, setCredentialReference] = useState("");
  const [credentialName, setCredentialName] = useState("");
  const [credentialSecret, setCredentialSecret] = useState("");

  useEffect(() => {
    void api("/api/models").then(value => setModels(value as ModelConfig[]));
    if (isAdmin) {
      void api("/api/credentials").then(value => setCredentials(value as Credential[]));
    }
  }, [api, isAdmin]);

  const createCredential = async () => {
    const value = await api("/api/credentials", {
      method: "POST",
      body: JSON.stringify({ name: credentialName, secret: credentialSecret }),
    }) as Credential;
    setCredentials(current => [...current, value]);
    setCredentialReference(value.reference);
    setCredentialName("");
    setCredentialSecret("");
  };

  const save = async () => {
    const existing = models.find(item => item.name === name);
    const value = await api("/api/models", {
      method: "POST",
      body: JSON.stringify({
        name,
        base_url: baseUrl,
        model,
        credential_reference: credentialReference,
        is_default: existing?.is_default ?? models.length === 0,
      }),
    }) as ModelConfig;
    setModels(current => [...current.filter(item => item.name !== value.name), value]);
    setName("");
    setModel("");
  };

  return <section className="panel">
    <header className="topbar"><div><span className="eyebrow">CONFIGURATION</span><h1>Model providers</h1></div></header>
    <div className="split">
      <div className="form-card">
        <h2>Connect a provider</h2>
        {!isAdmin && <p>Administrator access is required to change provider credentials.</p>}
        <input placeholder="Provider name" value={name} onChange={event => setName(event.target.value)} disabled={!isAdmin} />
        <input placeholder="HTTPS OpenAI-compatible base URL" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} disabled={!isAdmin} />
        <input placeholder="Model name" value={model} onChange={event => setModel(event.target.value)} disabled={!isAdmin} />
        <select value={credentialReference} onChange={event => setCredentialReference(event.target.value)} disabled={!isAdmin}>
          <option value="">Select a workspace credential</option>
          {credentials.filter(item => item.configured).map(item => <option value={item.reference} key={item.id}>{item.name}</option>)}
        </select>
        <button className="primary" onClick={() => void save()} disabled={!isAdmin || !name || !model || !credentialReference}><Settings size={15} />Save provider</button>
        <h2>Create write-only credential</h2>
        <input placeholder="Credential name" value={credentialName} onChange={event => setCredentialName(event.target.value)} disabled={!isAdmin} />
        <input type="password" placeholder="Provider API key" value={credentialSecret} onChange={event => setCredentialSecret(event.target.value)} disabled={!isAdmin} />
        <button className="secondary" onClick={() => void createCredential()} disabled={!isAdmin || !credentialName || !credentialSecret}>Store credential</button>
      </div>
      <div className="form-card">
        <h2>Configured models</h2>
        {models.map(item => <article className="model-row" key={item.id}><div><strong>{item.name}</strong><span>{item.model}</span></div>{item.is_default ? <em>Default</em> : null}</article>)}
      </div>
    </div>
  </section>;
}
