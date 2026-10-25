import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { makeApi, TaskView, loadBase, loadModelSettings, readStoredModelSettings, storeModelSettings, ModelSettings, saveBase, fetchBackendCredential } from "./api";

type Chat = TaskView & { reply?: string };

function titleFor(goal: string) {
  const clean = goal.trim().replace(/\s+/g, " ");
  return clean.length > 56 ? `${clean.slice(0, 56)}…` : clean || "New conversation";
}

export function App() {
  const stageRef = useRef<HTMLIFrameElement>(null);
  const [api, setApi] = useState(() => makeApi(loadBase()));
  const [chats, setChats] = useState<Chat[]>([]);
  const [active, setActive] = useState<Chat | null>(null);
  const [prompt, setPrompt] = useState("");
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendUrl, setBackendUrl] = useState(api.base);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(() => loadModelSettings());
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaStatus, setOllamaStatus] = useState("");
  const [settingsError, setSettingsError] = useState("");
  const [checkingOllama, setCheckingOllama] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [providerReady, setProviderReady] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState("");

  // The Tauri shell exposes the backend credential over local IPC. Load it before
  // issuing any HTTP request; otherwise the authenticated API correctly returns 401.
  useEffect(() => {
    let mounted = true;
    void fetchBackendCredential().then((credential) => {
      if (!mounted) return;
      if (credential) {
        setAuthError("");
      } else {
        setAuthError("Raphael could not load its backend credential. Restart the desktop app and make sure its managed backend is running.");
      }
      setAuthReady(true);
    }).catch((err: unknown) => {
      if (!mounted) return;
      setAuthError(err instanceof Error ? err.message : "Could not load the backend credential.");
      setAuthReady(true);
    });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!authReady || authError) return;
    let mounted = true;
    api.listTasks().then((items) => mounted && setChats(items.slice(0, 24))).catch(() => void 0);
    return () => { mounted = false; };
  }, [api, authReady, authError]);

  useEffect(() => {
    if (!authReady) return;
    if (authError) {
      setSettingsError(authError);
      setProviderReady(true);
      return;
    }
    let mounted = true;
    const stored = readStoredModelSettings();
    const initial = stored ?? loadModelSettings();
    setModelSettings(initial);
    setProviderReady(false);
    setSettingsError("");
    void (async () => {
      try {
        const remote = await api.getModelSettings();
        if (!mounted) return;
        if (stored) {
          const applied = await api.saveModelSettings(stored);
          if (!mounted) return;
          setModelSettings(applied);
          storeModelSettings(applied);
        } else {
          setModelSettings(remote);
          storeModelSettings(remote);
        }
      } catch (err) {
        if (!mounted) return;
        setModelSettings(initial);
        if (stored) {
          setSettingsError(err instanceof Error ? err.message : "Could not apply saved model settings.");
        }
      } finally {
        if (mounted) setProviderReady(true);
      }
    })();
    return () => { mounted = false; };
  }, [api, authReady, authError]);

  // Discover installed models automatically on startup and whenever the Ollama endpoint changes.
  useEffect(() => {
    if (!providerReady || !authReady || authError) return;
    let cancelled = false;
    const endpoint = modelSettings.endpoint;
    const timer = window.setTimeout(() => {
      setCheckingOllama(true);
      setOllamaStatus("Discovering installed Ollama models…");
      setSettingsError("");
      void api.ollamaModels(endpoint)
        .then((result) => {
          if (cancelled) return;
          setOllamaModels(result.models);
          if (result.models.length > 0) {
            setOllamaStatus(`Connected. Found ${result.models.length} installed model${result.models.length === 1 ? "" : "s"}.`);
            setModelSettings((current) => ({
              ...current,
              model: current.model && result.models.includes(current.model) ? current.model : "",
            }));
          } else {
            setOllamaStatus("Connected, but no models are installed. Pull a model with Ollama first.");
            setModelSettings((current) => ({ ...current, model: "" }));
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setOllamaModels([]);
          setOllamaStatus("");
          setSettingsError(err instanceof Error ? err.message : "Could not discover Ollama models.");
        })
        .finally(() => {
          if (!cancelled) setCheckingOllama(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, providerReady, authReady, authError, modelSettings.endpoint]);

  const refreshChats = useCallback(async () => {
    try { setChats((await api.listTasks()).slice(0, 24)); } catch { /* keep the shell usable */ }
  }, [api]);

  const openChat = useCallback(async (chat: Chat) => {
    setActive(chat); setError(""); setReply(chat.reply ?? "");
    try {
      const result = await api.taskResult(chat.task_id);
      if (result.status === "FAILED") {
        setError(result.error || result.summary || "Raphael failed to complete this task.");
        setReply("");
      } else {
        setReply(result.reply || result.summary || "");
      }
    } catch (err) {
      setReply("");
      setError(err instanceof Error ? err.message : "Could not load this conversation.");
    }
  }, [api]);

  const waitForResult = useCallback(async (task: TaskView) => {
    let lastError = "";
    for (let i = 0; i < 180; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      try {
        const current = await api.getTask(task.task_id);
        if (!current.completed && current.state !== "FAILED" && current.state !== "CANCELLED") continue;
        const result = await api.taskResult(task.task_id);
        if (result.status === "FAILED" || current.state === "FAILED") {
          const message = result.error || result.summary || "Raphael failed to complete the task.";
          setReply(""); setError(message); setActive((prev) => ({ ...(prev ?? task), ...current }));
        } else if (result.status === "CANCELLED" || current.state === "CANCELLED") {
          setReply(""); setError("Task cancelled."); setActive((prev) => ({ ...(prev ?? task), ...current }));
        } else {
          const text = result.reply || result.summary || "Task completed without a response.";
          setReply(text); setError(""); setActive((prev) => ({ ...(prev ?? task), ...current, reply: text }));
        }
        setBusy(false); await refreshChats(); return;
      } catch (err) { lastError = err instanceof Error ? err.message : "Could not read the task result."; }
    }
    setBusy(false); setError(lastError || "Raphael did not return a response within 90 seconds.");
  }, [api, refreshChats]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const goal = prompt.trim();
    if (!goal || busy || !authReady || authError || !providerReady || !modelSettings.model.trim()) return;
    setError(""); setReply(""); setBusy(true); setActive(null);
    try {
      const task = await api.submitGoal(goal);
      setActive({ ...task, title: titleFor(goal) }); setPrompt(""); void waitForResult(task);
    } catch (err) { setBusy(false); setError(err instanceof Error ? err.message : "Could not send the prompt."); }
  };

  const newChat = () => {
    setActive(null); setReply(""); setPrompt(""); setError(""); setBusy(false);
  };

  const checkOllama = async () => {
    if (!authReady || authError) {
      setSettingsError(authError || "Backend authentication is not ready. Restart Raphael and try again.");
      return;
    }
    setCheckingOllama(true);
    setOllamaStatus("Refreshing installed Ollama models…");
    setSettingsError("");
    try {
      const result = await makeApi(backendUrl).ollamaModels(modelSettings.endpoint);
      setOllamaModels(result.models);
      if (result.models.length > 0) {
        setOllamaStatus(`Connected. Found ${result.models.length} installed model${result.models.length === 1 ? "" : "s"}.`);
        setModelSettings((current) => ({
          ...current,
          model: current.model && result.models.includes(current.model) ? current.model : "",
        }));
      } else {
        setOllamaStatus("Connected, but no models are installed. Pull a model with Ollama first.");
        setModelSettings((current) => ({ ...current, model: "" }));
      }
    } catch (err) {
      setOllamaModels([]);
      setOllamaStatus("");
      setSettingsError(err instanceof Error ? err.message : "Could not connect to Ollama.");
    } finally {
      setCheckingOllama(false);
    }
  };

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!authReady || authError) {
      setSettingsError(authError || "Backend authentication is not ready. Restart Raphael and try again.");
      return;
    }
    setSavingSettings(true);
    setSettingsError("");
    try {
      const nextApi = makeApi(backendUrl);
      const requested: ModelSettings = { ...modelSettings, provider: "ollama" };
      const saved = await nextApi.saveModelSettings(requested);
      saveBase(nextApi.base);
      storeModelSettings(saved);
      setApi(nextApi);
      setBackendUrl(nextApi.base);
      setModelSettings(saved);
      setSettingsOpen(false);
      setProviderReady(true);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "Could not save provider settings.");
    } finally {
      setSavingSettings(false);
    }
  };


  return (
    <div className="raphael-app">
      <iframe
        ref={stageRef}
        className="raphael-stage"
        title="Raphael visual background"
        src="/raphael-stage.html"
        aria-hidden="true"
      />
      <aside className="raphael-sidebar">
        <div className="brand-lockup"><div className="brand-mark">R</div><div><strong>Raphael</strong><span>AI operating layer</span></div></div>
        <button className="new-chat" onClick={newChat}>＋&nbsp; New chat</button><div className="sidebar-label">Recent</div>
        <div className="recent-list">{chats.length === 0 && <div className="recent-empty">No recent conversations</div>}{chats.map((chat) => <button key={chat.task_id} className={`recent-item ${active?.task_id === chat.task_id ? "active" : ""}`} onClick={() => void openChat(chat)}><span className="recent-title">{chat.title || "Untitled conversation"}</span><span className="recent-date">{new Date(chat.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span></button>)}</div>
        <div className="sidebar-bottom"><button className="side-action" onClick={() => setSettingsOpen(true)}>⚙&nbsp; Settings</button></div>
      </aside>
      <main className="raphael-main"><div className="top-fade" /><section className={`response-shell ${active || busy || reply || error ? "visible" : ""}`}><div className="response-card"><div className="response-avatar"><span>R</span></div><div className="response-content"><div className="response-head"><span>Raphael</span>{busy && <i className="thinking-dot" aria-label="Thinking" />}</div>{busy && !reply && !error && <div className="response-loading"><span /><span /><span /></div>}{error && <div className="response-error">{error}</div>}{!busy && !error && reply && <div className="response-text">{reply}</div>}</div></div></section>
        <form className="floating-composer" onSubmit={submit}><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Ask Raphael anything…" rows={1} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} /><button className="send-button" type="submit" disabled={!prompt.trim() || busy || !authReady || !!authError || !providerReady || !modelSettings.model.trim()} aria-label="Send"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12 20 4l-4.8 16-3.3-6.7L4 12Zm7.9 1.3 3.2 6.5L20 4l-8.1 9.3Z" /></svg></button></form>
      </main>
      {settingsOpen && (
        <div className="settings-backdrop" onMouseDown={(e) => e.currentTarget === e.target && setSettingsOpen(false)}>
          <form className="settings-panel" onSubmit={saveSettings}>
            <div className="settings-head">
              <div><span>Settings</span><small>Provider, model and generation</small></div>
              <button type="button" aria-label="Close settings" onClick={() => setSettingsOpen(false)}>×</button>
            </div>

            <section className="settings-section">
              <div className="settings-section-heading">
                <div><strong>Runtime</strong><p>Connection to the Raphael agent runtime.</p></div>
              </div>
              <div className="setting-field">
                <label htmlFor="backend-url">Backend endpoint</label>
                <input id="backend-url" value={backendUrl} onChange={(e) => setBackendUrl(e.target.value)} spellCheck={false} autoComplete="url" />
                <small>Default: http://127.0.0.1:8765</small>
              </div>
            </section>

            <section className="settings-section">
              <div className="settings-section-heading">
                <div><strong>Model provider</strong><p>Provider integrations are being added incrementally.</p></div>
                <span className="settings-chip">Local</span>
              </div>
              <div className="setting-field">
                <label htmlFor="model-provider">Provider</label>
                <select id="model-provider" value={modelSettings.provider} onChange={() => setModelSettings((current) => ({ ...current, provider: "ollama" }))}>
                  <option value="ollama">Ollama</option>
                </select>
                <small>Ollama is the only available provider in this version.</small>
              </div>
              <div className="setting-field">
                <label htmlFor="ollama-endpoint">Ollama endpoint</label>
                <div className="settings-inline">
                  <input id="ollama-endpoint" value={modelSettings.endpoint} onChange={(e) => setModelSettings((current) => ({ ...current, endpoint: e.target.value }))} spellCheck={false} autoComplete="url" />
                  <button type="button" className="check-button" onClick={() => void checkOllama()} disabled={checkingOllama}>{checkingOllama ? "Refreshing…" : "Refresh"}</button>
                </div>
                <small>Raphael discovers installed models automatically. Use Refresh after pulling a new model.</small>
              </div>
              <div className="setting-field">
                <label htmlFor="ollama-model">Installed model</label>
                <select id="ollama-model" value={modelSettings.model} onChange={(e) => setModelSettings((current) => ({ ...current, model: e.target.value }))} disabled={checkingOllama || ollamaModels.length === 0}>
                  <option value="">{checkingOllama ? "Discovering models…" : "Select an installed model"}</option>
                  {modelSettings.model && !ollamaModels.includes(modelSettings.model) && <option value={modelSettings.model}>{modelSettings.model} (not found in discovery)</option>}
                  {ollamaModels.map((model) => <option value={model} key={model}>{model}</option>)}
                </select>
                <small>{ollamaModels.length ? `${ollamaModels.length} installed model${ollamaModels.length === 1 ? "" : "s"} detected.` : "Start Ollama and install a model to populate this list."}</small>
              </div>
              {ollamaStatus && <div className="settings-status">{ollamaStatus}</div>}
            </section>

            <section className="settings-section">
              <div className="settings-section-heading">
                <div><strong>Generation settings</strong><p>These options are sent to Ollama for each model request.</p></div>
              </div>
              <div className="settings-grid">
                <div className="setting-field">
                  <label htmlFor="temperature">Temperature</label>
                  <input id="temperature" type="number" min="0" max="2" step="0.1" value={modelSettings.temperature} onChange={(e) => setModelSettings((current) => ({ ...current, temperature: Number(e.target.value) }))} />
                  <small>Randomness (0–2).</small>
                </div>
                <div className="setting-field">
                  <label htmlFor="top-p">Top P</label>
                  <input id="top-p" type="number" min="0.01" max="1" step="0.05" value={modelSettings.top_p} onChange={(e) => setModelSettings((current) => ({ ...current, top_p: Number(e.target.value) }))} />
                  <small>Nucleus sampling (0–1).</small>
                </div>
                <div className="setting-field">
                  <label htmlFor="top-k">Top K</label>
                  <input id="top-k" type="number" min="0" max="1000" step="1" value={modelSettings.top_k} onChange={(e) => setModelSettings((current) => ({ ...current, top_k: Number(e.target.value) }))} />
                  <small>Candidate items (0 disables).</small>
                </div>
                <div className="setting-field">
                  <label htmlFor="repeat-penalty">Repeat penalty</label>
                  <input id="repeat-penalty" type="number" min="0.1" max="3" step="0.05" value={modelSettings.repeat_penalty} onChange={(e) => setModelSettings((current) => ({ ...current, repeat_penalty: Number(e.target.value) }))} />
                  <small>Discourage repeated text.</small>
                </div>
                <div className="setting-field">
                  <label htmlFor="context-length">Context length</label>
                  <input id="context-length" type="number" min="256" max="262144" step="256" value={modelSettings.num_ctx} onChange={(e) => setModelSettings((current) => ({ ...current, num_ctx: Number(e.target.value) }))} />
                  <small>Context window size.</small>
                </div>
                <div className="setting-field">
                  <label htmlFor="max-output">Max output length</label>
                  <input id="max-output" type="number" min="1" max="32768" step="128" value={modelSettings.num_predict} onChange={(e) => setModelSettings((current) => ({ ...current, num_predict: Number(e.target.value) }))} />
                  <small>Maximum generated text length.</small>
                </div>
              </div>
            </section>

            {settingsError && <div className="settings-error" role="alert">{settingsError}</div>}
            <div className="settings-foot">
              <button type="button" className="ghost" onClick={() => setSettingsOpen(false)}>Cancel</button>
              <button className="save" disabled={savingSettings}>{savingSettings ? "Saving…" : "Save settings"}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
