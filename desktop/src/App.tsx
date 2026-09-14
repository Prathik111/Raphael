import { useCallback, useEffect, useRef, useState } from "react";
import { makeApi, loadBase } from "./api";

export function App() {
  const stageRef = useRef<HTMLIFrameElement>(null);
  const [api] = useState(() => makeApi(loadBase()));
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);

  const callStage = useCallback((name: "__raphaelReceiveReply" | "__raphaelReceiveError", text: string) => {
    const win = stageRef.current?.contentWindow as (Window & Record<string, unknown>) | null;
    const fn = win?.[name];
    if (typeof fn === "function") (fn as (value: string) => void)(text);
  }, []);

  const waitForResult = useCallback(async (taskId: string) => {
    let lastError = "";
    for (let i = 0; i < 180; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      try {
        const task = await api.getTask(taskId);
        if (!task.completed && task.state !== "FAILED" && task.state !== "CANCELLED") continue;
        const result = await api.taskResult(taskId);
        if (result.status === "FAILED" || task.state === "FAILED") {
          callStage("__raphaelReceiveError", result.error || result.summary || "Raphael failed to complete the task.");
        } else if (result.status === "CANCELLED" || task.state === "CANCELLED") {
          callStage("__raphaelReceiveError", "Task cancelled.");
        } else {
          callStage("__raphaelReceiveReply", result.reply || result.summary || "Task completed without a response.");
        }
        setBusy(false);
        return;
      } catch (err) {
        lastError = err instanceof Error ? err.message : "Could not read the task result.";
      }
    }
    setBusy(false);
    callStage("__raphaelReceiveError", lastError || "Raphael did not return a response within 90 seconds.");
  }, [api, callStage]);

  useEffect(() => {
    const onMessage = async (event: MessageEvent) => {
      if (event.source !== stageRef.current?.contentWindow) return;
      const data = event.data;
      if (!data || data.type !== "raphael-chat-submit" || typeof data.text !== "string") return;
      const text = data.text.trim();
      if (!text || busy) return;
      setBusy(true);
      try {
        const task = await api.submitGoal(text);
        void waitForResult(task.task_id);
      } catch (err) {
        setBusy(false);
        callStage("__raphaelReceiveError", err instanceof Error ? err.message : "Could not send the prompt.");
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [api, busy, callStage, waitForResult]);

  return (
    <div className={`raphael-app ${ready ? "is-ready" : ""}`} data-busy={busy ? "true" : "false"}>
      <iframe
        ref={stageRef}
        className="raphael-stage"
        title="Raphael visual interface"
        src="/raphael-stage.html"
        onLoad={() => window.setTimeout(() => setReady(true), 120)}
      />
      {!ready && <div className="intro-curtain" />}
    </div>
  );
}
