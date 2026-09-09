const DEFAULT_BASE = "http://127.0.0.1:8765";
const STORAGE_KEY = "ai-eco-backend-url";

function sanitizeBase(raw: string): string {
  const value = raw.trim().replace(/\/$/, "");
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Backend URL is invalid");
  }
  const host = url.hostname.toLowerCase();
  const loopback = host === "127.0.0.1" || host === "localhost" || host === "[::1]";
  if (url.protocol !== "https:" && !loopback) {
    throw new Error("Remote backends must use HTTPS");
  }
  if (!loopback && !url.hostname) throw new Error("Backend host is required");
  return url.toString().replace(/\/$/, "");
}

export function defaultBase(): string {
  return DEFAULT_BASE;
}

export function loadBase(): string {
  try {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get("api");
    if (fromQuery) return sanitizeBase(fromQuery);
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return sanitizeBase(stored);
  } catch {
    // Invalid or unavailable persistence falls back to the safe loopback default.
  }
  return DEFAULT_BASE;
}

export function saveBase(url: string): void {
  const safe = sanitizeBase(url);
  try {
    window.localStorage.setItem(STORAGE_KEY, safe);
  } catch {
    // The current runtime keeps using the edited value even without storage.
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

export async function backendStatus(): Promise<BackendStatus | null> {
  try {
    const core = window.__TAURI__?.core;
    if (!core) return null;
    return await core.invoke<BackendStatus>("backend_status");
  } catch {
    return null;
  }
}

// Session-only credential. Prefer Tauri IPC; never persist bearer tokens in renderer storage.
let apiCredential: string | null = null;

export function setApiCredential(credential: string | null): void {
  apiCredential = credential && credential.trim() ? credential.trim() : null;
}

export function loadApiCredential(): string | null {
  return apiCredential;
}

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

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const safeBase = sanitizeBase(base);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const credential = loadApiCredential();
  if (credential) headers.Authorization = `Bearer ${credential}`;

  let response: Response;
  try {
    response = await fetch(`${safeBase}${path}`, {
      ...init,
      headers: { ...headers, ...((init?.headers as Record<string, string>) ?? {}) },
    });
  } catch (err) {
    throw new Error(`Cannot reach the backend at ${safeBase}.`, { cause: err });
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new BackendError(response.status, `${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export function makeApi(base: string) {
  const safeBase = sanitizeBase(base);
  return {
    base: safeBase,
    health: () => request<{ status: string }>(safeBase, "/health"),
    submitGoal: (goal: string) => request<TaskView>(safeBase, "/tasks", {
      method: "POST", body: JSON.stringify({ goal }),
    }),
    listTasks: () => request<TaskView[]>(safeBase, "/tasks"),
    getTask: (id: string) => request<TaskView>(safeBase, `/tasks/${id}`),
    getTaskStatus: (id: string) => request<TaskStatus>(safeBase, `/tasks/${id}/status`),
    taskResult: (id: string) => request<TaskResult>(safeBase, `/tasks/${id}/result`),
    cancelTask: (id: string) => request<TaskView>(safeBase, `/tasks/${id}/cancel`, { method: "POST" }),
    taskEvents: (id: string, since = 0) => request<TaskEvent[]>(safeBase, `/tasks/${id}/events?since=${since}`),
    agentStatus: () => request<{ tasks: Record<string, number> }>(safeBase, "/agents/status"),
    awareness: () => request<Record<string, unknown>>(safeBase, "/awareness"),
    models: () => request<ModelInfo[]>(safeBase, "/models"),
    skills: () => request<SkillInfo[]>(safeBase, "/skills"),
  };
}

export type Api = ReturnType<typeof makeApi>;
