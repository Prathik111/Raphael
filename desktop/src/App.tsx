import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { makeApi, TaskView, loadBase } from "./api";

type StagePayload =
  | { type: "state"; thinking: boolean }
  | { type: "caption"; text: string }
  | { type: "clear-caption" };

export function App() {
  const stageRef = useRef<HTMLIFrameElement | null>(null);
  const [api] = useState(() => makeApi(loadBase()));
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("AI: connecting...");
  const [error, setError] = useState("");

  const postStage = useCallback((message: StagePayload) => {
    const frame = stageRef.current?.contentWindow;
    if (!frame) return;
    frame.postMessage({ source: "raphael-react", ...message }, "*");
  }, []);

  const syncStage = useCallback(() => {
    postStage({ type: "state", thinking: busy });
  }, [busy, postStage]);

  useEffect(() => {
    syncStage();
  }, [syncStage]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!event.data || event.data.source !== "raphael-stage") return;
      if (event.data.type === "ready") syncStage();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [syncStage]);

  const refreshStatus = useCallback(async () => {
    try {
      await api.health();
      setStatus(busy ? "AI: thinking" : "AI: ready");
    } catch {
      setStatus("AI: disconnected");
    }
  }, [api, busy]);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 3000);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  const waitForResult = useCallback(async (task: TaskView) => {
    let lastError = "";
    for (let i = 0; i < 180; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      try {
        const current = await api.getTask(task.task_id);
        if (!current.completed && current.state !== "FAILED" && current.state !== "CANCELLED") continue;

        const result = await api.taskResult(task.task_id);
        setBusy(false);

        if (result.status === "FAILED" || current.state === "FAILED") {
          const message = result.error || result.summary || "Raphael failed to complete the task.";
          setError(message);
          postStage({ type: "state", thinking: false });
          postStage({ type: "caption", text: `[Error] ${message}` });
          setStatus("AI: error");
        } else if (result.status === "CANCELLED" || current.state === "CANCELLED") {
          setError("Task cancelled.");
          postStage({ type: "state", thinking: false });
          postStage({ type: "caption", text: "[Cancelled]" });
          setStatus("AI: ready");
        } else {
          const text = result.reply || result.summary || "Task completed without a response.";
          setError("");
          postStage({ type: "state", thinking: false });
          postStage({ type: "caption", text });
          setStatus("AI: ready");
        }
        return;
      } catch (err) {
        lastError = err instanceof Error ? err.message : "Could not read the task result.";
      }
    }

    setBusy(false);
    const message = lastError || "Raphael did not return a response within 90 seconds.";
    setError(message);
    postStage({ type: "state", thinking: false });
    postStage({ type: "caption", text: `[Error] ${message}` });
    setStatus("AI: timeout");
  }, [api, postStage]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const goal = prompt.trim();
    if (!goal || busy) return;

    setError("");
    setBusy(true);
    setStatus("AI: thinking");
    postStage({ type: "clear-caption" });
    postStage({ type: "state", thinking: true });

    try {
      const task = await api.submitGoal(goal);
      setPrompt("");
      void waitForResult(task);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not send the prompt.";
      setBusy(false);
      setError(message);
      setStatus("AI: disconnected");
      postStage({ type: "state", thinking: false });
      postStage({ type: "caption", text: `[Error] ${message}` });
    }
  };

  const newChat = () => {
    setBusy(false);
    setError("");
    setPrompt("");
    setStatus("AI: ready");
    postStage({ type: "state", thinking: false });
    postStage({ type: "clear-caption" });
  };

  return (
    <div className="raphael-app">
      <iframe
        ref={stageRef}
        className="raphael-stage"
        title="Raphael visual background"
        src="/raphael-stage.html"
        aria-hidden="true"
        onLoad={syncStage}
      />

      <header className="hud-brand" aria-label="Raphael">
        <div className="hud-brand-mark">R</div>
        <div>
          <div className="hud-brand-title">RAPHAEL</div>
          <div className="hud-brand-subtitle">AI OPERATING LAYER</div>
        </div>
      </header>

      <button className="new-chat-button" type="button" onClick={newChat}>
        NEW CHAT
      </button>

      <div className="hud-status" aria-live="polite">
        <span>{status}</span>
        {error ? <span className="hud-status-error"> // {error}</span> : null}
      </div>

      <form className="prototype-chat-bar" onSubmit={submit}>
        <input
          type="text"
          value={prompt}
          onChange={(event: ChangeEvent<HTMLInputElement>) => setPrompt(event.target.value)}
          placeholder="Ask Raphael..."
          autoComplete="off"
          aria-label="Ask Raphael"
          disabled={busy}
        />
        <button type="submit" disabled={!prompt.trim() || busy}>
          SEND
        </button>
      </form>
    </div>
  );
}
