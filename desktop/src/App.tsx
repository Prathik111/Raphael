import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Api,
  BackendError,
  BackendStatus,
  TaskEvent,
  TaskResult,
  TaskStatus,
  TaskView,
  backendStatus,
  defaultBase,
  fetchBackendCredential,
  loadApiCredential,
  loadBase,
  makeApi,
  saveBase,
  setApiCredential,
} from "./api";

type ConnState = "connecting" | "online" | "offline";

const STATE_DOT: Record<string, string> = {
  CREATED: "#8ab4f8",
  UNDERSTANDING: "#8ab4f8",
  AWARENESS: "#8ab4f8",
  RESEARCHING: "#c58af9",
  PLANNING: "#c58af9",
  WAITING_PERMISSION: "#fdd663",
  EXECUTING: "#81c995",
  VERIFYING: "#81c995",
  RECOVERING: "#f29900",
  COMPLETED: "#81c995",
  FAILED: "#f28b82",
  CANCELLED: "#9aa0a6",
};

const PHASE_LABEL: Record<string, string> = {
  CREATED: "Queued",
  UNDERSTANDING: "Understanding",
  AWARENESS: "Checking capabilities",
  RESEARCHING: "Researching",
  PLANNING: "Planning",
  WAITING_PERMISSION: "Waiting for permission",
  EXECUTING: "Working",
  VERIFYING: "Verifying",
  RECOVERING: "Recovering",
  COMPLETED: "Done",
  FAILED: "Failed",
  CANCELLED: "Stopped",
};

const SUGGESTIONS = [
  "List the files in my workspace and summarize what's there.",
  "Check git status of the workspace and tell me if it is clean.",
  "Create a notes file with three productivity tips.",
];

const css: Record<string, React.CSSProperties> = {
  page: {
    fontFamily:
      "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    background: "#212121",
    color: "#ececec",
    minHeight: "100vh",
    margin: 0,
    fontSize: 15,
  },
  shell: { display: "flex", minHeight: "100vh" },
  side: {
    width: 260,
    flexShrink: 0,
    background: "#171717",
    padding: "12px 10px",
    display: "flex",
    flexDirection: "column",
    gap: 4,
    position: "sticky",
    top: 0,
    height: "100vh",
    overflowY: "auto",
    boxSizing: "border-box",
  },
  newChat: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    background: "transparent",
    color: "#ececec",
    border: "1px solid #424242",
    borderRadius: 10,
    padding: "9px 12px",
    fontSize: 14,
    cursor: "pointer",
    marginBottom: 8,
  },
  convo: {
    width: "100%",
    textAlign: "left",
    background: "transparent",
    color: "#ececec",
    border: "none",
    borderRadius: 8,
    padding: "8px 10px",
    cursor: "pointer",
    fontSize: 14,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  sideFoot: { marginTop: "auto", paddingTop: 10, fontSize: 12, color: "#9aa0a6" },
  main: {
    flex: 1,
    minWidth: 0,
    display: "flex",
    flexDirection: "column",
    height: "100vh",
  },
  topbar: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "12px 20px",
    color: "#b4b4b4",
    fontSize: 14,
  },
  scroll: { flex: 1, overflowY: "auto", padding: "10px 0 20px" },
  column: { maxWidth: 768, margin: "0 auto", padding: "0 20px" },
  hero: { textAlign: "center", margin: "12vh 0 24px", fontSize: 28 },
  chips: {
    display: "flex",
    gap: 8,
    flexWrap: "wrap",
    justifyContent: "center",
  },
  chip: {
    background: "transparent",
    color: "#ececec",
    border: "1px solid #424242",
    borderRadius: 999,
    padding: "8px 14px",
    fontSize: 13,
    cursor: "pointer",
    maxWidth: "100%",
  },
  userRow: { display: "flex", justifyContent: "flex-end", margin: "14px 0" },
  userBubble: {
    background: "#2f2f2f",
    borderRadius: 20,
    padding: "10px 18px",
    maxWidth: "80%",
    whiteSpace: "pre-wrap",
    lineHeight: 1.6,
  },
  agentRow: { display: "flex", gap: 12, margin: "14px 0" },
  avatar: {
    width: 28,
    height: 28,
    borderRadius: 999,
    border: "1px solid #424242",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    fontSize: 14,
  },
  agentBody: { flex: 1, minWidth: 0, lineHeight: 1.7 },
  thinking: { color: "#9aa0a6", display: "flex", gap: 8, alignItems: "center" },
  spin: {
    width: 12,
    height: 12,
    borderRadius: 999,
    border: "2px solid #424242",
    borderTopColor: "#ececec",
    animation: "spin 0.9s linear infinite",
    flexShrink: 0,
  },
  toolsToggle: {
    background: "transparent",
    border: "1px solid #424242",
    color: "#b4b4b4",
    borderRadius: 999,
    padding: "4px 12px",
    fontSize: 12,
    cursor: "pointer",
    margin: "6px 0",
  },
  toolLine: {
    display: "flex",
    gap: 8,
    alignItems: "baseline",
    fontSize: 13,
    padding: "4px 0",
    borderBottom: "1px solid #2f2f2f",
  },
  mono: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5 },
  pre: {
    fontFamily: "ui-monospace, Consolas, monospace",
    fontSize: 12.5,
    background: "#171717",
    borderRadius: 8,
    padding: "10px 12px",
    overflowX: "auto",
    whiteSpace: "pre-wrap",
  },
  muted: { color: "#9aa0a6", fontSize: 13 },
  err: { color: "#f28b82" },
  ok: { color: "#81c995" },
  composerWrap: { padding: "0 0 18px" },
  composer: {
    background: "#2f2f2f",
    borderRadius: 24,
    padding: "10px 10px 10px 18px",
    display: "flex",
    alignItems: "flex-end",
    gap: 8,
  },
  area: {
    flex: 1,
    background: "transparent",
    border: "none",
    outline: "none",
    resize: "none",
    color: "#ececec",
    fontSize: 15,
    lineHeight: 1.5,
    maxHeight: 160,
    fontFamily: "inherit",
    padding: "6px 0",
  },
  send: {
    width: 34,
    height: 34,
    borderRadius: 999,
    border: "none",
    background: "#ececec",
    color: "#171717",
    fontSize: 16,
    cursor: "pointer",
    flexShrink: 0,
  },
  sendOff: { background: "#424242", color: "#9aa0a6", cursor: "default" },
  stop: { background: "#ececec", color: "#171717" },
  banner: {
    borderRadius: 10,
    padding: "10px 14px",
    margin: "0 0 12px",
    fontSize: 13,
  },
  ghost: {
    background: "transparent",
    color: "#8ab4f8",
    border: "1px solid #424242",
    borderRadius: 8,
    padding: "4px 10px",
    fontSize: 12,
    cursor: "pointer",
  },
  input: {
    width: "100%",
    boxSizing: "border-box",
    background: "#212121",
    color: "#ececec",
    border: "1px solid #424242",
    borderRadius: 8,
    padding: "8px 10px",
    fontSize: 13,
  },
};

/** Minimal markdown: fenced blocks, inline code, bold, lists, paragraphs.
 *  Pure React nodes -- model text is always rendered as text, so it can
 *  never inject markup. */
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let n = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const chunk = match[0];
    if (chunk.startsWith("**")) {
      out.push(<strong key={`${keyPrefix}-${n++}`}>{chunk.slice(2, -2)}</strong>);
    } else {
      out.push(
        <code
          key={`${keyPrefix}-${n++}`}
          style={{
            background: "#171717",
            padding: "1px 5px",
            borderRadius: 5,
          }}
        >
          {chunk.slice(1, -1)}
        </code>,
      );
    }
    last = match.index + chunk.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderMarkdown(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const parts = text.split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\n");
      const code = (nl >= 0 ? part.slice(nl + 1) : part).replace(
        /^\n+|\n+$/g,
        "",
      );
      out.push(
        <pre key={i} style={css.pre}>
          {code}
        </pre>,
      );
      return;
    }
    part.split(/\n{2,}/).forEach((block, j) => {
      const lines = block.split("\n").filter((l) => l.trim() !== "");
      if (lines.length === 0) return;
      if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
        out.push(
          <ul key={`${i}-${j}`} style={{ margin: "8px 0", paddingLeft: 22 }}>
            {lines.map((l, k) => (
              <li key={k}>
                {renderInline(l.replace(/^\s*[-*]\s+/, ""), `${i}-${j}-${k}`)}
              </li>
            ))}
          </ul>,
        );
      } else {
        out.push(
          <p key={`${i}-${j}`} style={{ margin: "8px 0" }}>
            {renderInline(block, `${i}-${j}`)}
          </p>,
        );
      }
    });
  });
  return out;
}

function fmtArgs(value: unknown, cap = 400): string {
  try {
    const text =
      typeof value === "string" ? value : JSON.stringify(value, null, 1);
    return text.length > cap ? text.slice(0, cap) + "…" : text;
  } catch {
    return String(value);
  }
}

interface ToolCallView {
  callId: string;
  tool: string;
  args: unknown;
  status: "running" | "ok" | "failed" | "denied";
  snippet: string;
  reason: string;
}

function buildToolCalls(events: TaskEvent[]): ToolCallView[] {
  const byCall = new Map<string, ToolCallView>();
  const order: string[] = [];
  const get = (callId: string, tool: string): ToolCallView => {
    let view = byCall.get(callId);
    if (!view) {
      view = { callId, tool, args: undefined, status: "running", snippet: "", reason: "" };
      byCall.set(callId, view);
      order.push(callId);
    }
    return view;
  };
  for (const event of events) {
    const p = event.payload as Record<string, unknown>;
    const callId = String(p.call_id ?? p.tool_call_id ?? "");
    if (!callId) continue;
    if (event.type === "ToolRequested") {
      const view = get(callId, String(p.tool ?? "?"));
      view.args = p.arguments;
    } else if (event.type === "ToolCompleted") {
      const view = get(callId, String(p.tool ?? "?"));
      view.status = "ok";
      view.snippet = String(p.output_snippet ?? "");
    } else if (event.type === "ToolFailed") {
      const view = get(callId, String(p.tool ?? "?"));
      view.status = "failed";
      view.snippet = String(p.output_snippet ?? "");
      view.reason = String(p.error ?? "");
    } else if (event.type === "PermissionDenied") {
      const view = get(callId, String(p.tool ?? "?"));
      view.status = "denied";
      view.reason = String(p.reason ?? "");
    }
  }
  return order.map((id) => byCall.get(id) as ToolCallView);
}

export function App() {
  const [base, setBase] = useState(loadBase);
  const [api, setApi] = useState<Api>(() => makeApi(loadBase()));
  const [conn, setConn] = useState<ConnState>("connecting");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [tasks, setTasks] = useState<TaskView[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskView | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<{ provider_id: string }[]>([]);
  const [backend, setBackend] = useState<BackendStatus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [draftBase, setDraftBase] = useState(base);
  const [draftCredential, setDraftCredential] = useState("");
  const [showTools, setShowTools] = useState(true);
  const [showRaw, setShowRaw] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const online = conn === "online";

  const refreshMeta = useCallback(
    async (client: Api) => {
      try {
        // Bearer credential first: the shell reads it from the backend's
        // credential file; manual entry (Settings) covers external backends.
        await fetchBackendCredential();
        await client.health();
        setConn("online");
        client
          .models()
          .then(setModels)
          .catch(() => setModels([]));
        backendStatus().then(setBackend);
      } catch (err) {
        setConn("offline");
        if (err instanceof BackendError && err.status === 401) {
          setError(
            "The backend rejected the API credential. Enter the current one " +
              "in Settings (backend log shows its file location).",
          );
        }
      }
    },
    [],
  );

  const refreshTasks = useCallback(async (client: Api) => {
    try {
      setTasks(await client.listTasks());
    } catch (err) {
      setConn("offline");
      if (err instanceof Error) setError(err.message);
    }
  }, []);

  useEffect(() => {
    void refreshMeta(api);
    const timer = window.setInterval(() => void refreshMeta(api), 8000);
    return () => window.clearInterval(timer);
  }, [api, refreshMeta]);

  useEffect(() => {
    if (!online) return;
    void refreshTasks(api);
    const timer = window.setInterval(() => void refreshTasks(api), 3000);
    return () => window.clearInterval(timer);
  }, [api, online, refreshTasks]);

  useEffect(() => {
    if (!selectedId || !online) {
      setDetail(null);
      setEvents([]);
      setResult(null);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const [view, evts] = await Promise.all([
          api.getTask(selectedId),
          api.taskEvents(selectedId).catch(() => [] as TaskEvent[]),
        ]);
        if (cancelled) return;
        setDetail(view);
        setEvents(evts);
        if (view.completed) {
          api
            .taskResult(selectedId)
            .then((r) => {
              if (!cancelled) setResult(r);
            })
            .catch((err) => {
              if (!(err instanceof BackendError && err.status === 503)) {
                if (!cancelled) setResult(null);
              }
            });
        } else {
          setResult(null);
        }
      } catch {
        // Transient: stale view stays; conn poll reports outages.
      }
    };
    void poll();
    const timer = window.setInterval(poll, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [api, selectedId, online]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length, result?.status, selectedId]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy || !online) return;
    setBusy(true);
    try {
      const created = await api.submitGoal(text);
      setDraft("");
      setSelectedId(created.task_id);
      await refreshTasks(api);
    } catch (err) {
      setError(err instanceof Error ? err.message : "send failed");
    } finally {
      setBusy(false);
    }
  }, [api, draft, online, refreshTasks, busy]);

  const stop = useCallback(async () => {
    if (!selectedId) return;
    try {
      await api.cancelTask(selectedId);
      await refreshTasks(api);
    } catch (err) {
      setError(err instanceof Error ? err.message : "stop failed");
    }
  }, [api, refreshTasks, selectedId]);

  const newChat = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setEvents([]);
    setResult(null);
  }, []);

  const applyBase = useCallback(() => {
    const cleaned = draftBase.trim().replace(/\/$/, "") || defaultBase();
    saveBase(cleaned);
    setBase(cleaned);
    setApi(makeApi(cleaned));
    setConn("connecting");
    newChat();
  }, [draftBase, newChat]);

  const toolCalls = useMemo(() => buildToolCalls(events), [events]);
  const running = detail !== null && !detail.completed;
  const sorted = useMemo(() => [...tasks].reverse(), [tasks]);
  const veredicts = useMemo(
    () => events.filter((e) => e.type.startsWith("Verification")),
    [events],
  );

  return (
    <div style={css.page}>
      <style>{"@keyframes spin { to { transform: rotate(360deg); } }"}</style>
      <div style={css.shell}>
        <aside style={css.side}>
          <button style={css.newChat} onClick={newChat}>
            <span style={{ fontSize: 16 }}>＋</span> New chat
          </button>
          {sorted.length === 0 ? (
            <p style={css.muted}>
              {online ? "No conversations yet." : "Offline."}
            </p>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {sorted.map((task) => (
                <li key={task.task_id}>
                  <button
                    style={{
                      ...css.convo,
                      background:
                        task.task_id === selectedId ? "#2f2f2f" : "transparent",
                    }}
                    onClick={() => setSelectedId(task.task_id)}
                    title={`${task.title} — ${task.state}`}
                  >
                    <span
                      style={{
                        display: "inline-block",
                        width: 7,
                        height: 7,
                        borderRadius: 999,
                        background: STATE_DOT[task.state] ?? "#9aa0a6",
                        marginRight: 8,
                        flexShrink: 0,
                      }}
                    />
                    {task.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div style={css.sideFoot}>
            <div style={{ marginBottom: 6 }}>
              <span
                style={{
                  color:
                    conn === "online"
                      ? "#81c995"
                      : conn === "connecting"
                        ? "#fdd663"
                        : "#f28b82",
                }}
              >
                {conn === "online"
                  ? "● Connected"
                  : conn === "connecting"
                    ? "● Connecting…"
                    : "● Offline"}
              </span>
              {backend && backend.state !== "unknown" && (
                <span>
                  {" · "}
                  {backend.state === "managed"
                    ? "backend auto-started"
                    : backend.state === "external"
                      ? "external backend"
                      : "backend failed"}
                </span>
              )}
            </div>
            <div style={{ marginBottom: 6 }}>
              {models.length > 0 ? (
                <code style={css.mono}>{models[0].provider_id}</code>
              ) : (
                <span style={{ color: "#fdd663" }}>no model</span>
              )}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                style={css.ghost}
                onClick={() => {
                  setDraftBase(base);
                  setShowSettings((v) => !v);
                }}
              >
                Settings
              </button>
              <button
                style={css.ghost}
                onClick={() => {
                  void refreshMeta(api);
                  void refreshTasks(api);
                }}
              >
                Retry
              </button>
            </div>
            {showSettings && (
              <div style={{ marginTop: 8 }}>
                <input
                  style={css.input}
                  value={draftBase}
                  onChange={(e) => setDraftBase(e.target.value)}
                  placeholder={defaultBase()}
                  aria-label="Backend URL"
                />
                <input
                  style={{ ...css.input, marginTop: 6 }}
                  type="password"
                  value={draftCredential}
                  onChange={(e) => setDraftCredential(e.target.value)}
                  placeholder={
                    loadApiCredential()
                      ? "API credential saved (enter to replace)"
                      : "API credential (for external backends)"
                  }
                  aria-label="API credential"
                />
                <div style={{ marginTop: 6 }}>
                  <button
                    style={css.ghost}
                    onClick={() => {
                      if (draftCredential.trim()) setApiCredential(draftCredential);
                      setDraftCredential("");
                      applyBase();
                    }}
                  >
                    Connect
                  </button>
                </div>
              </div>
            )}
          </div>
        </aside>

        <main style={css.main}>
          <div style={css.topbar}>
            <span style={{ color: "#ececec", fontWeight: 600 }}>
              AI Ecosystem
            </span>
            {detail && (
              <span>
                · {PHASE_LABEL[detail.state] ?? detail.state}
                {running && (
                  <span
                    style={{
                      ...css.spin,
                      display: "inline-block",
                      marginLeft: 8,
                      verticalAlign: "middle",
                    }}
                  />
                )}
              </span>
            )}
          </div>

          <div style={css.scroll}>
            <div style={css.column}>
              {error && (
                <div
                  style={{ ...css.banner, background: "#3a2320" }}
                  role="alert"
                >
                  {error}{" "}
                  <button style={css.ghost} onClick={() => setError(null)}>
                    Dismiss
                  </button>
                </div>
              )}

              {online && models.length === 0 && (
                <div
                  style={{ ...css.banner, background: "#38300f" }}
                  role="note"
                >
                  <strong>No model configured</strong> — tasks will fail.
                  Set the model endpoint + key (
                  <code style={css.mono}>AI_ECO_MODEL_*</code>) and restart{" "}
                  <code style={css.mono}>ai-ecosystem-serve</code>.
                </div>
              )}

              {!selectedId || !detail ? (
                <>
                  <h1 style={css.hero}>What can I do for you?</h1>
                  <div style={css.chips}>
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        style={css.chip}
                        disabled={!online}
                        onClick={() => {
                          setDraft(s);
                        }}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  {!online && (
                    <p style={{ ...css.muted, textAlign: "center" }}>
                      Backend unreachable at {base}. Start{" "}
                      <code style={css.mono}>ai-ecosystem-serve</code>, then
                      press Retry.
                    </p>
                  )}
                </>
              ) : (
                <>
                  <div style={css.userRow}>
                    <div style={css.userBubble}>{detail.title}</div>
                  </div>

                  <div style={css.agentRow}>
                    <div style={css.avatar}>✦</div>
                    <div style={css.agentBody}>
                      {result ? (
                        <>
                          {result.reply ? (
                            renderMarkdown(result.reply)
                          ) : (
                            <>
                              {renderMarkdown(
                                result.summary.replace(
                                  /^(Assistant|AI Ecosystem):\s*/,
                                  "",
                                ) || "(done)",
                              )}
                              {result.error && (
                                <p style={css.err}>
                                  <code style={css.mono}>
                                    {result.error.slice(0, 600)}
                                  </code>
                                </p>
                              )}
                            </>
                          )}
                          <div style={css.muted}>
                            {Object.keys(result.step_states).length > 0 &&
                              `${Object.values(result.step_states).filter((s) => s === "SUCCEEDED").length}/${Object.keys(result.step_states).length} steps · `}
                            {result.verification_status &&
                              `verified ${result.verification_status} · `}
                            {result.timings.total_s !== undefined &&
                              `${result.timings.total_s}s`}
                          </div>
                        </>
                      ) : (
                        <div style={css.thinking}>
                          <span style={css.spin} />
                          {PHASE_LABEL[detail.state] ?? detail.state}…
                        </div>
                      )}

                      {toolCalls.length > 0 && (
                        <div>
                          <button
                            style={css.toolsToggle}
                            onClick={() => setShowTools((v) => !v)}
                          >
                            {showTools ? "▾" : "▸"} Used {toolCalls.length}{" "}
                            tool{toolCalls.length === 1 ? "" : "s"}
                          </button>
                          {showTools &&
                            toolCalls.map((call) => (
                              <div key={call.callId} style={css.toolLine}>
                                <span
                                  style={{
                                    color:
                                      call.status === "ok"
                                        ? "#81c995"
                                        : call.status === "running"
                                          ? "#8ab4f8"
                                          : "#f28b82",
                                  }}
                                >
                                  {call.status === "ok"
                                    ? "✓"
                                    : call.status === "running"
                                      ? "…"
                                      : call.status === "denied"
                                        ? "⛔"
                                        : "✗"}
                                </span>
                                <code style={css.mono}>{call.tool}</code>
                                <span style={css.muted}>
                                  {fmtArgs(call.args, 160)}
                                </span>
                              </div>
                            ))}
                          {showTools &&
                            toolCalls
                              .filter((c) => c.snippet || c.reason)
                              .map((call) => (
                                <pre key={`${call.callId}-out`} style={css.pre}>
                                  {fmtArgs(
                                    call.snippet || call.reason,
                                    500,
                                  )}
                                </pre>
                              ))}
                        </div>
                      )}

                      {veredicts.length > 0 && (
                        <div style={{ ...css.muted, marginTop: 6 }}>
                          {veredicts.map((e) => {
                            const ok = e.type === "VerificationPassed";
                            const p = e.payload as Record<string, unknown>;
                            return (
                              <div key={e.seq}>
                                <span style={ok ? css.ok : css.err}>
                                  {ok ? "✓" : "✗"}
                                </span>{" "}
                                check {ok ? "passed" : "did not pass"}
                                {p.step_id ? ` · ${String(p.step_id)}` : ""}
                                {p.reason
                                  ? ` — ${fmtArgs(p.reason, 200)}`
                                  : ""}
                              </div>
                            );
                          })}
                        </div>
                      )}

                      <div style={{ marginTop: 8 }}>
                        <button
                          style={css.ghost}
                          onClick={() => setShowRaw((v) => !v)}
                        >
                          {showRaw
                            ? "Hide activity log"
                            : `Activity log (${events.length})`}
                        </button>
                        {showRaw &&
                          events.map((e) => (
                            <div key={e.seq} style={css.toolLine}>
                              <span style={css.mono}>[{e.seq}]</span>
                              <span style={css.mono}>{e.type}</span>
                              <span style={css.muted}>
                                {fmtArgs(e.payload, 220)}
                              </span>
                            </div>
                          ))}
                      </div>
                    </div>
                  </div>
                </>
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          <div style={css.composerWrap}>
            <div style={css.column}>
              <div style={css.composer}>
                <textarea
                  style={css.area}
                  rows={1}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  placeholder={
                    !online
                      ? "Backend offline…"
                      : running
                        ? "Agent is working — Stop it to send a new task"
                        : "Message the agent…"
                  }
                  disabled={!online || busy || running}
                  aria-label="Message the agent"
                />
                {running ? (
                  <button
                    style={{ ...css.send, ...css.stop }}
                    onClick={() => void stop()}
                    aria-label="Stop"
                    title="Stop this task"
                  >
                    ■
                  </button>
                ) : (
                  <button
                    style={{
                      ...css.send,
                      ...(!online || busy || !draft.trim()
                        ? css.sendOff
                        : {}),
                    }}
                    onClick={() => void send()}
                    disabled={!online || busy || !draft.trim()}
                    aria-label="Send"
                    title="Send"
                  >
                    ↑
                  </button>
                )}
              </div>
              <p
                style={{
                  ...css.muted,
                  textAlign: "center",
                  fontSize: 11,
                  margin: "8px 0 0",
                }}
              >
                The agent runs tools on your machine — review the activity
                above.
              </p>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
