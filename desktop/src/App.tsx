import type { CSSProperties } from "react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api,
  ApprovalView,
  BackendError,
  BackendStatus,
  TaskEvent,
  TaskResult,
  TaskView,
  backendStatus,
  fetchBackendCredential,
  loadBase,
  makeApi,
  saveBase,
} from "./api";

type Connection = "connecting" | "online" | "offline";

type ToolCall = {
  callId: string;
  tool: string;
  args?: unknown;
  status: "running" | "ok" | "failed" | "denied";
  snippet: string;
  reason: string;
};

const STATE_META: Record<string, { label: string; tone: "blue" | "green" | "amber" | "red" | "muted" }> = {
  CREATED: { label: "Queued", tone: "blue" },
  UNDERSTANDING: { label: "Understanding", tone: "blue" },
  AWARENESS: { label: "Checking system", tone: "blue" },
  RESEARCHING: { label: "Researching", tone: "blue" },
  PLANNING: { label: "Planning", tone: "amber" },
  WAITING_PERMISSION: { label: "Permission needed", tone: "amber" },
  EXECUTING: { label: "Executing", tone: "green" },
  VERIFYING: { label: "Verifying", tone: "green" },
  RECOVERING: { label: "Recovering", tone: "amber" },
  COMPLETED: { label: "Completed", tone: "green" },
  FAILED: { label: "Failed", tone: "red" },
  CANCELLED: { label: "Cancelled", tone: "muted" },
};

const SUGGESTIONS = [
  "Review the workspace and summarize its structure",
  "Check git status and explain any uncommitted changes",
  "Create a concise project notes file with the key next steps",
];

const css = `
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  --bg: #080b11;
  --panel: #0e131c;
  --panel-2: #121925;
  --border: rgba(255,255,255,.08);
  --border-strong: rgba(255,255,255,.14);
  --text: #eef2f8;
  --muted: #8f9bad;
  --faint: #647083;
  --accent: #7c9cff;
  --accent-2: #6ee7d0;
  --danger: #ff7777;
  --warning: #f5c36a;
  --shadow: 0 20px 70px rgba(0,0,0,.35);
}
* { box-sizing: border-box; }
html, body, #root { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); }
button, textarea, input { font: inherit; }
button { color: inherit; }
.app { min-height: 100vh; display: flex; background: radial-gradient(circle at 75% 5%, rgba(124,156,255,.12), transparent 30%), var(--bg); }
.sidebar { width: 282px; padding: 18px; border-right: 1px solid var(--border); background: rgba(9,12,18,.88); backdrop-filter: blur(22px); display: flex; flex-direction: column; gap: 18px; }
.brand { display: flex; align-items: center; gap: 12px; padding: 3px 4px; }
.logo { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; background: linear-gradient(135deg, #9bb3ff, #6ee7d0); color: #08101b; font-weight: 900; box-shadow: 0 12px 30px rgba(124,156,255,.22); }
.brand-copy strong { display: block; font-size: 14px; letter-spacing: .01em; }
.brand-copy span { display: block; margin-top: 2px; color: var(--faint); font-size: 11px; }
.new-task { width: 100%; border: 1px solid var(--border-strong); background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.015)); border-radius: 12px; padding: 11px 13px; cursor: pointer; text-align: left; font-size: 13px; font-weight: 650; }
.new-task:hover { border-color: rgba(124,156,255,.45); background: rgba(124,156,255,.08); }
.section-label { color: var(--faint); font-size: 10px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; padding: 0 5px; }
.task-list { display: flex; flex-direction: column; gap: 6px; overflow: auto; min-height: 0; }
.task-item { border: 1px solid transparent; background: transparent; padding: 10px 11px; border-radius: 10px; cursor: pointer; text-align: left; }
.task-item:hover { background: rgba(255,255,255,.035); }
.task-item.active { background: rgba(124,156,255,.09); border-color: rgba(124,156,255,.22); }
.task-title { font-size: 12px; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.task-meta { display: flex; gap: 6px; align-items: center; margin-top: 6px; color: var(--faint); font-size: 10px; }
.dot { width: 6px; height: 6px; border-radius: 999px; display: inline-block; }
.dot.blue { background: var(--accent); } .dot.green { background: var(--accent-2); } .dot.amber { background: var(--warning); } .dot.red { background: var(--danger); } .dot.muted { background: #647083; }
.side-footer { margin-top: auto; border-top: 1px solid var(--border); padding-top: 14px; color: var(--faint); font-size: 11px; line-height: 1.6; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar { height: 64px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 26px; background: rgba(8,11,17,.72); backdrop-filter: blur(20px); }
.topbar-left { display: flex; gap: 10px; align-items: center; min-width: 0; }
.page-title { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.page-subtitle { color: var(--faint); font-size: 11px; margin-left: 8px; }
.status { display: inline-flex; align-items: center; gap: 7px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 999px; font-size: 11px; color: var(--muted); }
.workspace { width: min(1120px, calc(100vw - 340px)); margin: 0 auto; padding: 30px 26px 34px; display: flex; flex-direction: column; gap: 18px; }
.hero { padding: 8px 0 3px; }
.eyebrow { display: inline-flex; gap: 7px; align-items: center; color: var(--accent-2); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.hero h1 { margin: 10px 0 7px; font-size: clamp(25px, 3vw, 38px); letter-spacing: -.03em; line-height: 1.08; }
.hero p { margin: 0; color: var(--muted); max-width: 680px; line-height: 1.55; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 11px; }
.stat { background: rgba(14,19,28,.85); border: 1px solid var(--border); border-radius: 14px; padding: 14px; box-shadow: var(--shadow); }
.stat-label { color: var(--faint); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; font-weight: 800; }
.stat-value { margin-top: 8px; font-size: 19px; font-weight: 750; }
.stat-note { margin-top: 4px; color: var(--muted); font-size: 10px; }
.panel { border: 1px solid var(--border); background: rgba(14,19,28,.82); border-radius: 16px; box-shadow: var(--shadow); overflow: hidden; }
.panel-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.panel-title { font-size: 12px; font-weight: 800; letter-spacing: .02em; }
.panel-subtitle { color: var(--faint); font-size: 10px; }
.panel-body { padding: 16px; }
.composer { display: flex; gap: 10px; align-items: flex-end; }
.composer textarea { flex: 1; min-height: 94px; max-height: 220px; resize: vertical; color: var(--text); background: #0a0e15; border: 1px solid var(--border-strong); border-radius: 12px; padding: 13px 14px; outline: none; line-height: 1.55; font-size: 13px; }
.composer textarea:focus { border-color: rgba(124,156,255,.6); box-shadow: 0 0 0 4px rgba(124,156,255,.08); }
.send { border: 0; min-width: 92px; height: 42px; padding: 0 14px; border-radius: 11px; background: linear-gradient(135deg, #9bb3ff, #6ee7d0); color: #08101b; font-size: 12px; font-weight: 850; cursor: pointer; }
.send:disabled { opacity: .45; cursor: default; }
.suggestions { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 11px; }
.suggestion { border: 1px solid var(--border); background: rgba(255,255,255,.02); border-radius: 999px; color: var(--muted); padding: 7px 10px; cursor: pointer; font-size: 10px; }
.suggestion:hover { border-color: rgba(124,156,255,.35); color: var(--text); }
.banner { padding: 12px 14px; border-radius: 11px; font-size: 11px; line-height: 1.5; }
.banner.error { color: #ffd1d1; background: rgba(255,119,119,.08); border: 1px solid rgba(255,119,119,.2); }
.banner.warn { color: #fbe3a7; background: rgba(245,195,106,.07); border: 1px solid rgba(245,195,106,.18); }
.detail-grid { display: grid; grid-template-columns: 1.25fr .75fr; gap: 12px; }
.activity { display: flex; flex-direction: column; gap: 10px; max-height: 460px; overflow: auto; }
.activity-row { display: grid; grid-template-columns: 8px 1fr auto; gap: 9px; align-items: start; padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,.045); }
.activity-row:last-child { border-bottom: 0; }
.activity-dot { width: 7px; height: 7px; margin-top: 4px; border-radius: 999px; background: #6f7c90; }
.activity-copy strong { display: block; font-size: 11px; }
.activity-copy span { display: block; margin-top: 3px; color: var(--muted); font-size: 10px; line-height: 1.4; }
.activity-time { color: var(--faint); font-size: 9px; white-space: nowrap; }
.tool { border: 1px solid var(--border); background: rgba(255,255,255,.018); border-radius: 11px; padding: 10px; }
.tool + .tool { margin-top: 8px; }
.tool-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tool-name { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 10px; }
.tool-status { font-size: 9px; color: var(--muted); }
.tool-snippet { margin-top: 7px; white-space: pre-wrap; color: var(--muted); font: 10px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace; max-height: 100px; overflow: auto; }
.empty { padding: 22px; color: var(--faint); text-align: center; font-size: 11px; }
.result { white-space: pre-wrap; line-height: 1.65; font-size: 12px; color: #dbe3f0; }
.kv { display: grid; grid-template-columns: 110px 1fr; gap: 7px 12px; font-size: 10px; }
.kv dt { color: var(--faint); } .kv dd { margin: 0; color: var(--muted); word-break: break-word; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.action { background: transparent; border: 1px solid var(--border-strong); border-radius: 9px; padding: 7px 10px; font-size: 10px; color: var(--muted); cursor: pointer; }
.action:hover { color: var(--text); }
.action.approve { border-color: rgba(110,231,208,.4); color: var(--accent-2); }
.action.deny { border-color: rgba(255,119,119,.4); color: var(--danger); }
.approval { border: 1px solid rgba(245,195,106,.3); background: rgba(245,195,106,.05); border-radius: 11px; padding: 10px; }
.approval + .approval { margin-top: 8px; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(2,4,8,.7); backdrop-filter: blur(10px); display: grid; place-items: center; padding: 20px; z-index: 20; }
.modal { width: min(560px, 100%); background: #0d131d; border: 1px solid var(--border-strong); border-radius: 16px; box-shadow: 0 30px 90px rgba(0,0,0,.55); }
.modal-body { padding: 16px; }
.field { margin-top: 13px; }
.field label { display: block; color: var(--muted); font-size: 10px; margin-bottom: 6px; }
.field input { width: 100%; background: #090d13; border: 1px solid var(--border); border-radius: 10px; color: var(--text); padding: 9px 10px; outline: none; font-size: 12px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--border); }
@media (max-width: 980px) { .sidebar { width: 225px; } .workspace { width: calc(100vw - 225px); } .detail-grid { grid-template-columns: 1fr; } }
@media (max-width: 760px) { .sidebar { display: none; } .workspace { width: 100vw; padding: 22px 16px 30px; } .topbar { padding: 0 16px; } .grid { grid-template-columns: repeat(2, minmax(0,1fr)); } .composer { flex-direction: column; } .send { width: 100%; } }
`;

function stateMeta(state: string) {
  return STATE_META[state] ?? { label: state, tone: "muted" as const };
}

function relativeTime(date: string): string {
  const ms = Math.max(0, Date.now() - new Date(date).getTime());
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function buildTools(events: TaskEvent[]): ToolCall[] {
  const map = new Map<string, ToolCall>();
  for (const e of events) {
    const p = e.payload ?? {};
    const callId = String(p.call_id ?? p.tool_call_id ?? "");
    if (!callId) continue;
    const tool = String(p.tool ?? "unknown");
    const current = map.get(callId) ?? { callId, tool, status: "running", snippet: "", reason: "" };
    if (e.type === "ToolRequested") { current.args = p.arguments; current.status = "running"; }
    if (e.type === "ToolCompleted") { current.status = "ok"; current.snippet = String(p.output_snippet ?? ""); }
    if (e.type === "ToolFailed") { current.status = "failed"; current.snippet = String(p.output_snippet ?? ""); current.reason = String(p.error ?? ""); }
    if (e.type === "PermissionDenied") { current.status = "denied"; current.reason = String(p.reason ?? ""); }
    map.set(callId, current);
  }
  return [...map.values()];
}

function eventLabel(type: string): string {
  return type.replaceAll("_", " ").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().replace(/^./, c => c.toUpperCase());
}

function fmtArgs(value: unknown, cap = 500): string {
  try {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 1);
    return text.length > cap ? text.slice(0, cap) + "…" : text;
  } catch {
    return String(value);
  }
}

function markdownToText(text: string): string {
  return text.replace(/```[a-zA-Z0-9_-]*\n?/g, "").replace(/`/g, "");
}

export function App() {
  const [base, setBase] = useState(loadBase);
  const [api, setApi] = useState<Api>(() => makeApi(loadBase()));
  const [conn, setConn] = useState<Connection>("connecting");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<{ provider_id: string }[]>([]);
  const [backend, setBackend] = useState<BackendStatus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [draftBase, setDraftBase] = useState(base);
  const [agentCounts, setAgentCounts] = useState<Record<string, number>>({});
  const [approvals, setApprovals] = useState<ApprovalView[]>([]);
  const pollRef = useRef<number | null>(null);

  const selected = useMemo(() => tasks.find(t => t.task_id === selectedId) ?? null, [tasks, selectedId]);
  const tools = useMemo(() => buildTools(events), [events]);
  const activeTasks = useMemo(() => tasks.filter(t => !["COMPLETED", "FAILED", "CANCELLED"].includes(t.state)).length, [tasks]);
  const completedTasks = useMemo(() => tasks.filter(t => t.state === "COMPLETED").length, [tasks]);
  const currentState = selected ? stateMeta(selected.state) : null;

  const refreshTasks = useCallback(async (client = api) => {
    try {
      const [nextTasks, nextAgent, nextModels, nextSkills] = await Promise.all([
        client.listTasks(), client.agentStatus(), client.models(), client.skills(),
      ]);
      setTasks(nextTasks);
      setAgentCounts(nextAgent.tasks ?? {});
      setModels(nextModels.map(m => ({ provider_id: m.provider_id })));
      setSkillCount(nextSkills.length);
      setConn("online");
      setError(null);
      if (!selectedId && nextTasks[0]) setSelectedId(nextTasks[0].task_id);
    } catch (err) {
      setConn("offline");
      if (err instanceof Error) setError(err.message);
    }
  }, [api, selectedId]);

  const [skillCount, setSkillCount] = useState(0);

  const refreshDetail = useCallback(async (id: string, client = api) => {
    try {
      const [detail, ev] = await Promise.all([client.getTask(id), client.taskEvents(id, 0)]);
      setTasks(current => current.some(t => t.task_id === id) ? current.map(t => t.task_id === id ? detail : t) : [detail, ...current]);
      setEvents(ev);
      if (["COMPLETED", "FAILED", "CANCELLED"].includes(detail.state)) {
        try { setResult(await client.taskResult(id)); } catch { setResult(null); }
      } else setResult(null);
      setConn("online");
    } catch (err) {
      if (err instanceof BackendError && err.status === 404) setError("That task no longer exists.");
      else if (err instanceof Error) setError(err.message);
    }
  }, [api]);

  useEffect(() => {
    let active = true;
    void (async () => {
      await fetchBackendCredential();
      try {
        await api.health();
        if (!active) return;
        setConn("online");
        await refreshTasks(api);
        const status = await backendStatus();
        if (active) setBackend(status);
      } catch {
        if (active) setConn("offline");
      }
    })();
    return () => { active = false; };
  }, [api, refreshTasks]);

  useEffect(() => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    if (!selectedId) return;
    pollRef.current = window.setInterval(() => {
      void refreshDetail(selectedId);
      void refreshTasks(api);
    }, 1800);
    void refreshDetail(selectedId);
    return () => { if (pollRef.current !== null) window.clearInterval(pollRef.current); };
  }, [selectedId, api, refreshDetail, refreshTasks]);

  useEffect(() => {
    if (conn !== "online") { setApprovals([]); return; }
    let active = true;
    const poll = async () => {
      try {
        const pending = await api.approvals();
        if (active) setApprovals(pending);
      } catch { /* transient: keep last known list */ }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [api, conn]);

  const decideApproval = async (id: string, approved: boolean) => {
    try {
      if (approved) await api.approveApproval(id);
      else await api.denyApproval(id);
      setApprovals(current => current.filter(a => a.id !== id));
      if (selectedId) await refreshDetail(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to record decision");
    }
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const goal = draft.trim();
    if (!goal || busy) return;
    setBusy(true);
    setError(null);
    try {
      const task = await api.submitGoal(goal);
      setTasks(current => [task, ...current.filter(t => t.task_id !== task.task_id)]);
      setSelectedId(task.task_id);
      setDraft("");
      setConn("online");
    } catch (err) {
      setConn("offline");
      setError(err instanceof Error ? err.message : "Unable to submit task");
    } finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!selectedId) return;
    try { await api.cancelTask(selectedId); await refreshDetail(selectedId); }
    catch (err) { setError(err instanceof Error ? err.message : "Unable to stop task"); }
  };

  const applySettings = () => {
    try {
      const safe = draftBase.trim();
      saveBase(safe);
      const nextApi = makeApi(safe);
      setBase(nextApi.base);
      setApi(nextApi);
      setShowSettings(false);
      setConn("connecting");
    } catch (err) { setError(err instanceof Error ? err.message : "Invalid backend URL"); }
  };

  const agentPool = Object.values(agentCounts).reduce((a, b) => a + b, 0);
  const connectionLabel = conn === "online" ? "Backend online" : conn === "connecting" ? "Connecting" : "Backend offline";

  return (
    <>
      <style>{css}</style>
      <div className="app">
        <aside className="sidebar">
          <div className="brand"><div className="logo">R</div><div className="brand-copy"><strong>Raphael</strong><span>Autonomous operating agent</span></div></div>
          <button className="new-task" onClick={() => { setSelectedId(null); setEvents([]); setResult(null); setError(null); }}>＋ New task</button>
          <div className="section-label">Recent tasks</div>
          <div className="task-list">
            {tasks.length === 0 ? <div className="empty">Your task history will appear here.</div> : tasks.map(task => {
              const meta = stateMeta(task.state);
              return <button key={task.task_id} className={`task-item ${selectedId === task.task_id ? "active" : ""}`} onClick={() => setSelectedId(task.task_id)}>
                <div className="task-title">{task.title || "Untitled task"}</div>
                <div className="task-meta"><span className={`dot ${meta.tone}`} />{meta.label} · {relativeTime(task.created_at)}</div>
              </button>;
            })}
          </div>
          <div className="side-footer"><div>{backend?.detail || base}</div><div style={{ marginTop: 4 }}>{models.length} model · {skillCount} skills · {tasks.length} tasks</div></div>
        </aside>

        <main className="main">
          <header className="topbar">
            <div className="topbar-left"><div className="page-title">{selected?.title || "Operator console"}</div>{selected && currentState && <span className="page-subtitle">{currentState.label}</span>}</div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}><div className="status"><span className={`dot ${conn === "online" ? "green" : conn === "connecting" ? "blue" : "red"}`} />{connectionLabel}</div><button className="action" onClick={() => { setDraftBase(base); setShowSettings(true); }}>Settings</button></div>
          </header>

          <section className="workspace">
            {!selected && <div className="hero"><div className="eyebrow">● PACE runtime</div><h1>What should Raphael take care of?</h1><p>Describe the outcome. Raphael can inspect the system, plan work, request permissions, execute through the tool gateway, verify results, and retain useful context.</p></div>}

            <div className="grid">
              <div className="stat"><div className="stat-label">Connection</div><div className="stat-value">{conn === "online" ? "Ready" : conn === "connecting" ? "Starting" : "Offline"}</div><div className="stat-note">Authenticated local runtime</div></div>
              <div className="stat"><div className="stat-label">Active</div><div className="stat-value">{activeTasks}</div><div className="stat-note">Tasks in progress</div></div>
              <div className="stat"><div className="stat-label">Completed</div><div className="stat-value">{completedTasks}</div><div className="stat-note">Successful tasks in history</div></div>
              <div className="stat"><div className="stat-label">Agent pool</div><div className="stat-value">{agentPool}</div><div className="stat-note">Currently tracked by runtime</div></div>
            </div>

            <form className="panel" onSubmit={submit}>
              <div className="panel-header"><div><div className="panel-title">Give Raphael a goal</div><div className="panel-subtitle">Natural language in, verified execution out.</div></div></div>
              <div className="panel-body"><div className="composer"><textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="Example: inspect the repo, summarize the architecture, and tell me what should be fixed next…" onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") void submit(); }} /><button className="send" disabled={!draft.trim() || busy}>{busy ? "Starting…" : "Run goal"}</button></div><div className="suggestions">{SUGGESTIONS.map(text => <button type="button" key={text} className="suggestion" onClick={() => setDraft(text)}>{text}</button>)}</div></div>
            </form>

            {error && <div className="banner error">{error}</div>}

            {approvals.length > 0 && <div className="panel"><div className="panel-header"><div><div className="panel-title">Approval needed ({approvals.length})</div><div className="panel-subtitle">Review the exact action — approval binds to these arguments only</div></div></div><div className="panel-body">{approvals.map(a => <div key={a.id} className="approval"><div className="tool-top"><div className="tool-name">{a.tool}</div><div className="tool-status">{a.risk}</div></div><div className="tool-snippet">{JSON.stringify(a.arguments, null, 1).slice(0, 600)}</div><div className="tool-snippet">{a.reason}</div><div className="actions" style={{ marginTop: 8 }}><button className="action approve" onClick={() => void decideApproval(a.id, true)}>Approve</button><button className="action deny" onClick={() => void decideApproval(a.id, false)}>Deny</button></div></div>)}</div></div>}

            {selected && <div className="detail-grid">
              <div className="panel"><div className="panel-header"><div><div className="panel-title">Execution activity</div><div className="panel-subtitle">Durable task events from the runtime</div></div><div className="actions">{!selected.completed && <button className="action" onClick={() => void cancel()}>Stop task</button>}<button className="action" onClick={() => void refreshDetail(selected.task_id)}>Refresh</button></div></div><div className="panel-body"><div className="activity">{events.length === 0 ? <div className="empty">Waiting for runtime events…</div> : events.slice().reverse().map((e, i) => { const payload = e.payload as Record<string, unknown>; return <div className="activity-row" key={`${e.seq}-${i}`}><span className="activity-dot" /><div className="activity-copy"><strong>{eventLabel(e.type)}</strong><span>{String(payload.message ?? payload.reason ?? "Runtime event recorded.")}</span></div><span className="activity-time">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>; })}</div></div></div>
              <div className="panel"><div className="panel-header"><div><div className="panel-title">Task overview</div><div className="panel-subtitle">State, verification, and runtime</div></div></div><div className="panel-body"><dl className="kv"><dt>Status</dt><dd>{currentState?.label ?? selected.state}</dd><dt>Task ID</dt><dd>{selected.task_id}</dd><dt>Created</dt><dd>{new Date(selected.created_at).toLocaleString()}</dd><dt>Tools used</dt><dd>{tools.length}</dd><dt>Model providers</dt><dd>{models.length}</dd><dt>Backend</dt><dd>{base}</dd></dl></div></div>
            </div>}

            {selected && tools.length > 0 && <div className="panel"><div className="panel-header"><div><div className="panel-title">Tool execution</div><div className="panel-subtitle">What the execution gateway has done</div></div></div><div className="panel-body">{tools.map(tool => <div key={tool.callId} className="tool"><div className="tool-top"><div className="tool-name">{tool.tool}</div><div className="tool-status">{tool.status}</div></div>{tool.args !== undefined && <div className="tool-snippet">{fmtArgs(tool.args)}</div>}{tool.snippet && <div className="tool-snippet">{tool.snippet}</div>}{tool.reason && <div className="tool-snippet">{tool.reason}</div>}</div>)}</div></div>}

            {result && <div className="panel"><div className="panel-header"><div><div className="panel-title">Verified result</div><div className="panel-subtitle">Final response and verification state</div></div><div className="status"><span className={`dot ${result.verification_status === "PASSED" ? "green" : result.status === "FAILED" ? "red" : "amber"}`} />{result.verification_status}</div></div><div className="panel-body"><div className="result">{markdownToText(result.reply || result.summary || result.error || "No final response was returned.")}</div></div></div>}
          </section>
        </main>

        {showSettings && <div className="modal-backdrop" onMouseDown={() => setShowSettings(false)}><div className="modal" onMouseDown={e => e.stopPropagation()}><div className="panel-header"><div><div className="panel-title">Runtime settings</div><div className="panel-subtitle">Choose the authenticated backend Raphael should use.</div></div></div><div className="modal-body"><div className="field"><label>Backend URL</label><input value={draftBase} onChange={e => setDraftBase(e.target.value)} placeholder="http://127.0.0.1:8765" /></div><div className="banner warn" style={{ marginTop: 12 }}>Remote endpoints must use HTTPS. Loopback HTTP is allowed for the local runtime.</div></div><div className="modal-foot"><button className="action" onClick={() => setShowSettings(false)}>Cancel</button><button className="send" style={{ height: 36 }} onClick={applySettings}>Save settings</button></div></div></div>}
      </div>
    </>
  );
}

const _unused: CSSProperties | null = null;
