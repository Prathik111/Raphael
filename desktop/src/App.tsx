import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { makeApi, TaskView, loadBase } from "./api";

type Chat = TaskView & { reply?: string };
type StageCommand = { type: "raphael-stage"; action: "move" | "center"; x?: number; y?: number; scale?: number };

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
  const [introDone, setIntroDone] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendUrl, setBackendUrl] = useState(api.base);

  const sendStage = useCallback((cmd: StageCommand) => {
    stageRef.current?.contentWindow?.postMessage(cmd, "*");
  }, []);

  const focusResponse = useCallback(() => {
    sendStage({ type: "raphael-stage", action: "move", x: 0.73, y: 0.43, scale: 0.62 });
  }, [sendStage]);

  useEffect(() => {
    let mounted = true;
    api.listTasks().then((items) => mounted && setChats(items.slice(0, 24))).catch(() => void 0);
    return () => { mounted = false; };
  }, [api]);

  const refreshChats = useCallback(async () => {
    try { setChats((await api.listTasks()).slice(0, 24)); } catch { /* keep the shell usable */ }
  }, [api]);

  const openChat = useCallback(async (chat: Chat) => {
    setActive(chat); setError(""); setReply(chat.reply ?? "");
    if (chat.reply) { focusResponse(); return; }
    try {
      const result = await api.taskResult(chat.task_id);
      setReply(result.reply || result.summary || "");
      focusResponse();
    } catch { setReply(""); }
  }, [api, focusResponse]);

  const waitForResult = useCallback(async (task: TaskView) => {
    for (let i = 0; i < 300; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 650));
      try {
        const current = await api.getTask(task.task_id);
        if (!current.completed && current.state !== "FAILED" && current.state !== "CANCELLED") continue;
        const result = await api.taskResult(task.task_id);
        const text = result.reply || result.summary || result.error || "";
        setReply(text);
        setActive((prev) => ({ ...(prev ?? task), ...current, reply: text }));
        setBusy(false);
        await refreshChats();
        return;
      } catch { /* result persistence can briefly lag task state */ }
    }
    setBusy(false); setError("Raphael did not return a response in time.");
  }, [api, refreshChats]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const goal = prompt.trim();
    if (!goal || busy) return;
    setError(""); setReply(""); setBusy(true); setActive(null); focusResponse();
    try {
      const task = await api.submitGoal(goal);
      setActive({ ...task, title: titleFor(goal) });
      setPrompt("");
      void waitForResult(task);
    } catch (err) {
      setBusy(false); setError(err instanceof Error ? err.message : "Could not send the prompt.");
    }
  };

  const newChat = () => {
    setActive(null); setReply(""); setPrompt(""); setError(""); setBusy(false);
    sendStage({ type: "raphael-stage", action: "center" });
  };

  const saveSettings = (event: FormEvent) => {
    event.preventDefault();
    try {
      const next = makeApi(backendUrl);
      setApi(next); window.localStorage.setItem("ai-eco-backend-url", next.base); setSettingsOpen(false);
    } catch (err) { setError(err instanceof Error ? err.message : "Invalid backend URL."); }
  };

  return (
    <div className={`raphael-app ${introDone ? "is-ready" : ""}`}>
      <iframe ref={stageRef} className="raphael-stage" title="Raphael visual core" src="/raphael-stage.html" onLoad={() => window.setTimeout(() => setIntroDone(true), 120)} />

      <aside className="raphael-sidebar">
        <div className="brand-lockup">
          <div className="brand-mark">R</div>
          <div><strong>Raphael</strong><span>AI operating layer</span></div>
        </div>
        <button className="new-chat" onClick={newChat}>＋&nbsp; New chat</button>
        <div className="sidebar-label">Recent</div>
        <div className="recent-list">
          {chats.length === 0 && <div className="recent-empty">No recent conversations</div>}
          {chats.map((chat) => (
            <button key={chat.task_id} className={`recent-item ${active?.task_id === chat.task_id ? "active" : ""}`} onClick={() => void openChat(chat)}>
              <span className="recent-title">{chat.title || "Untitled conversation"}</span>
              <span className="recent-date">{new Date(chat.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-bottom"><button className="side-action" onClick={() => setSettingsOpen(true)}>⚙&nbsp; Settings</button></div>
      </aside>

      <main className="raphael-main">
        <div className="top-fade" />
        <section className={`response-shell ${active || busy || reply || error ? "visible" : ""}`}>
          <div className="response-card">
            <div className="response-avatar"><span>R</span></div>
            <div className="response-content">
              <div className="response-head"><span>Raphael</span>{busy && <i className="thinking-dot" aria-label="Thinking" />}</div>
              {busy && !reply && !error && <div className="response-loading"><span /><span /><span /></div>}
              {error && <div className="response-error">{error}</div>}
              {!busy && !error && reply && <div className="response-text">{reply}</div>}
            </div>
          </div>
        </section>

        <form className="floating-composer" onSubmit={submit}>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Ask Raphael anything…" rows={1} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} />
          <button className="send-button" type="submit" disabled={!prompt.trim() || busy} aria-label="Send">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12 20 4l-4.8 16-3.3-6.7L4 12Zm7.9 1.3 3.2 6.5L20 4l-8.1 9.3Z" /></svg>
          </button>
        </form>
      </main>

      {!introDone && <div className="intro-curtain" />}

      {settingsOpen && (
        <div className="settings-backdrop" onMouseDown={(e) => e.currentTarget === e.target && setSettingsOpen(false)}>
          <form className="settings-panel" onSubmit={saveSettings}>
            <div className="settings-head"><span>Settings</span><button type="button" onClick={() => setSettingsOpen(false)}>×</button></div>
            <label>Backend</label><input value={backendUrl} onChange={(e) => setBackendUrl(e.target.value)} spellCheck={false} />
            <div className="settings-foot"><button type="button" className="ghost" onClick={() => setSettingsOpen(false)}>Cancel</button><button className="save">Save</button></div>
          </form>
        </div>
      )}
    </div>
  );
}
