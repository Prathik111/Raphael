// Typed client for the local Python runtime API.
// The UI never executes tools, decides permissions, or holds credentials:
// every function below is a plain fetch against the loopback backend.
// The backend is loopback-only (no auth); the base URL is configurable
// so operators can point at a non-default port.

const DEFAULT_BASE = "http://127.0.0.1:8765";
const STORAGE_KEY = "ai-eco-backend-url";

export function defaultBase(): string {
  return DEFAULT_BASE;
}

export function loadBase(): string {
  try {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get("api");
    if (fromQuery) return fromQuery.replace(/\/$/, "");
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return stored.replace(/\/$/, "");
  } catch {
    // Storage/query unavailable (privacy mode): fall back to default.
  }
  return DEFAULT_BASE;
}

export function saveBase(url: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, url.replace(/\/$/, ""));
  } catch {
    // Non-fatal: the session simply keeps using the edited value.
  }
}

export interface TaskView {
  task_id: string;
  title: string;
  state: string;
  created_at: string;
  completed: boolean;
}

export interface TaskStatus {
  task_id: string;
  state: string;
  next: string[];
}

export interface TaskEvent {
  seq: number;
  type: string;
  task_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TaskResult {
  task_id: string;
  status: string;
  summary: string;
  reply: string;
  step_states: Record<string, string>;
  verification_status: string;
  memory_ids: string[];
  evidence: string[];
  timings: Record<string, number>;
  error: string;
}

export interface ModelInfo {
  provider_id: string;
  capabilities: Record<string, unknown>;
}

export interface SkillInfo {
  name: string;
  version: string;
  status: string;
}

export interface BackendStatus {
  /** "managed" (spawned by the app), "external" (already running),
   *  "failed" (auto-start failed), "unknown" (browser dev / old shell). */
  state: "managed" | "external" | "failed" | "unknown";
  url: string;
  detail: string;
}

declare global {
  interface Window {
    __TAURI__?: {
      core: {
        invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T>;
      };
    };
  }
}

/** Ask the Tauri shell how the backend is running (null outside Tauri). */
export async function backendStatus(): Promise<BackendStatus | null> {
  try {
    const core = window.__TAURI__?.core;
    if (!core) return null;
    return await core.invoke<BackendStatus>("backend_status");
  } catch {
    return null;
  }
}

const CRED_KEY = "ai-eco-backend-credential";
let apiCredential: string | null = null;

export function setApiCredential(credential: string | null): void {
  apiCredential =
    credential && credential.trim() ? credential.trim() : null;
  try {
    if (apiCredential) window.localStorage.setItem(CRED_KEY, apiCredential);
    else window.localStorage.removeItem(CRED_KEY);
  } catch {
    // Private mode: memory-only credential still works for the session.
  }
}

export function loadApiCredential(): string | null {
  if (apiCredential) return apiCredential;
  try {
    apiCredential = window.localStorage.getItem(CRED_KEY);
  } catch {
    apiCredential = null;
  }
  return apiCredential;
}

/** Bearer credential minted by the backend; the shell reads it from the
 *  credential file so the UI never asks the operator for it. Manual entry
 *  (Settings) covers externally-run backends. */
export async function fetchBackendCredential(): Promise<string | null> {
  try {
    const core = window.__TAURI__?.core;
    if (!core) return loadApiCredential();
    const credential = await core.invoke<string>("backend_api_credential");
    setApiCredential(credential);
    return credential;
  } catch {
    return loadApiCredential();
  }
}

export class BackendError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  base: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const credential = loadApiCredential();
  if (credential) headers["Authorization"] = `Bearer ${credential}`;
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers: { ...headers, ...((init?.headers as Record<string, string>) ?? {}) },
    });
  } catch (err) {
    throw new Error(
      `cannot reach the backend at ${base} — is ai-ecosystem-serve running?`,
      { cause: err },
    );
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new BackendError(response.status, `${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export function makeApi(base: string) {
  return {
    base,
    health: () => request<{ status: string }>(base, "/health"),
    submitGoal: (goal: string) =>
      request<TaskView>(base, "/tasks", {
        method: "POST",
        body: JSON.stringify({ goal }),
      }),
    listTasks: () => request<TaskView[]>(base, "/tasks"),
    getTask: (id: string) => request<TaskView>(base, `/tasks/${id}`),
    getTaskStatus: (id: string) =>
      request<TaskStatus>(base, `/tasks/${id}/status`),
    taskResult: (id: string) =>
      request<TaskResult>(base, `/tasks/${id}/result`),
    cancelTask: (id: string) =>
      request<TaskView>(base, `/tasks/${id}/cancel`, { method: "POST" }),
    taskEvents: (id: string, since = 0) =>
      request<TaskEvent[]>(base, `/tasks/${id}/events?since=${since}`),
    agentStatus: () =>
      request<{ tasks: Record<string, number> }>(base, "/agents/status"),
    awareness: () => request<Record<string, unknown>>(base, "/awareness"),
    models: () => request<ModelInfo[]>(base, "/models"),
    skills: () => request<SkillInfo[]>(base, "/skills"),
  };
}

export type Api = ReturnType<typeof makeApi>;
