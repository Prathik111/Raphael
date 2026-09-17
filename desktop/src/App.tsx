import { useCallback, useEffect, useRef, useState } from "react";
import { fetchBackendCredential, loadBase } from "./api";

type StageMessage =
  | { type: "backend"; base: string; token: string | null }
  | { type: "new-chat" };

export function App() {
  const stageRef = useRef<HTMLIFrameElement | null>(null);
  const [base] = useState(() => loadBase());
  const [credential, setCredential] = useState<string | null>(null);

  const postStage = useCallback((message: StageMessage) => {
    stageRef.current?.contentWindow?.postMessage(
      { source: "raphael-react", ...message },
      "*",
    );
  }, []);

  const syncBackend = useCallback(() => {
    postStage({ type: "backend", base, token: credential });
  }, [base, credential, postStage]);

  useEffect(() => {
    let mounted = true;
    void fetchBackendCredential().then((token) => {
      if (mounted) setCredential(token);
    });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    syncBackend();
  }, [syncBackend]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.data?.source === "raphael-stage" && event.data.type === "ready") {
        syncBackend();
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [syncBackend]);

  return (
    <div className="raphael-app">
      <iframe
        ref={stageRef}
        className="raphael-stage"
        title="Raphael HUD"
        src="/raphael-stage.html"
        onLoad={syncBackend}
      />
    </div>
  );
}
