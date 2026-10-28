import { useCallback, useEffect, useState } from "react";
import { Api } from "./api";

export interface McpConfig {
  id: string;
  transport: "stdio" | "http";
  command: string;
  args: string[];
  url: string;
  env_names: string[];
  enabled: boolean;
  timeout_s: number;
}
export interface ServerView { config: McpConfig; status: string; error: string; tools: string[] }
export interface Suggestion { config: McpConfig; source: string }
export interface Community { name: string; description: string; url: string; config: Partial<McpConfig> }
const empty: McpConfig = { id: "", transport: "stdio", command: "", args: [], url: "", env_names: [], enabled: false, timeout_s: 30 };

export function McpSettings({ api }: { api: Api }) {
  const [servers, setServers] = useState<ServerView[]>([]);
  const [discovered, setDiscovered] = useState<Suggestion[]>([]);
  const [community, setCommunity] = useState<Community[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<McpConfig>({ ...empty });
  const [argumentsText, setArgumentsText] = useState("[]");
  const [environmentText, setEnvironmentText] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [reviewId, setReviewId] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [state, discovery] = await Promise.all([api.mcpServers(), api.discoverMcp()]);
      setServers(state.servers); setDiscovered(discovery.discovered);
      setCommunity(discovery.community); setWarnings(discovery.warnings);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not load MCP servers."); }
  }, [api]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api.mcpServers().then((state) => { if (!cancelled) setServers(state.servers); }).catch(() => void 0);
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [api]);

  const edit = (config: Partial<McpConfig>) => {
    const next = { ...empty, ...config, enabled: false };
    setDraft(next); setArgumentsText(JSON.stringify(next.args)); setEnvironmentText(next.env_names.join(", "));
    setReviewed(false); setReviewId(""); setError("");
  };
  const save = async () => {
    setBusy(true); setError("");
    try {
      const args: unknown = JSON.parse(argumentsText);
      if (!Array.isArray(args) || !args.every((item) => typeof item === "string")) throw new Error("Arguments must be a JSON list of strings.");
      const config = { ...draft, enabled: false, args: draft.transport === "stdio" ? args : [],
        command: draft.transport === "stdio" ? draft.command : "", url: draft.transport === "http" ? draft.url : "",
        env_names: draft.transport === "stdio" ? environmentText.split(",").map((name) => name.trim()).filter(Boolean) : [] };
      setServers((await api.saveMcp(config)).servers); setDraft({ ...empty }); setArgumentsText("[]"); setEnvironmentText("");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not save MCP server."); }
    finally { setBusy(false); }
  };
  const toggle = async (id: string, enabled: boolean) => {
    setBusy(true); setError("");
    try {
      setServers((await (enabled ? api.disableMcp(id) : api.enableMcp(id))).servers);
      setReviewId(""); setReviewed(false);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update MCP server."); }
    finally { setBusy(false); }
  };

  return <section className="settings-section mcp-settings">
    <div className="settings-section-heading"><div><strong>MCP tools</strong><p>Connect external tools and let Raphael select them for your tasks.</p></div><button type="button" className="ghost" disabled={busy} onClick={() => void refresh()}>Discover</button></div>
    <p className="mcp-note">Tools from enabled servers are discovered automatically. Calls require your approval. Discovery never starts software.</p>
    {servers.length === 0 && <p className="mcp-note">No servers configured. Choose a community server, import a discovered one, or add your own below.</p>}
    {servers.map((server) => <article className="mcp-server" key={server.config.id}>
      <div className="mcp-server-head"><strong>{server.config.id}</strong><span className="settings-chip">{server.status}</span></div>
      <code>{server.config.transport === "stdio" ? `${server.config.command} ${server.config.args.join(" ")}` : server.config.url}</code>
      {server.config.env_names.length > 0 && <p className="mcp-note">Required environment names: {server.config.env_names.join(", ")}</p>}
      <p className="mcp-note">{server.tools.length} tools available</p>
      {server.tools.length > 0 && <details><summary>Discovered tools</summary><ul>{server.tools.map((tool) => <li key={tool}>{tool.split(".").slice(3).join(".")}</li>)}</ul></details>}
      {server.error && <p className="settings-error" role="alert">{server.error}</p>}
      <div className="mcp-actions">
        {server.config.enabled ? <button type="button" className="ghost" disabled={busy} onClick={() => void toggle(server.config.id, true)}>Disable</button> : <><button type="button" className="ghost" disabled={busy} onClick={() => edit(server.config)}>Edit</button><button type="button" className="save" disabled={busy} onClick={() => { setReviewId(server.config.id); setReviewed(false); }}>Enable</button></>}
        {server.config.enabled && server.status === "error" && <button type="button" className="ghost" disabled={busy} onClick={() => void toggle(server.config.id, false)}>Retry connection</button>}
      </div>
      {reviewId === server.config.id && <div className="mcp-review">
        <p>Enabling runs this server with your account's access. Package launchers may download software. Tool arguments may be sent to this server; calls still require approval.</p>
        <label className="mcp-consent"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />I reviewed this command or address and allow this server to receive tool arguments that contain no credentials.</label>
        <button type="button" className="save" disabled={!reviewed || busy} onClick={() => void toggle(server.config.id, false)}>{busy ? "Connecting..." : "Enable and discover tools"}</button>
      </div>}
    </article>)}
    {discovered.length > 0 && <div className="mcp-library"><strong>Found on this computer</strong>{discovered.map((item) => <div className="mcp-choice" key={`${item.source}:${item.config.id}`}><div><b>{item.config.id}</b><small>{item.source}</small></div><button type="button" className="ghost" disabled={busy} onClick={() => edit(item.config)}>Review</button></div>)}</div>}
    <div className="mcp-library"><strong>Community servers</strong><p className="mcp-note">Starting points you can review and customize. Requires Node.js/npx or uv/uvx. Other community servers work through the same form.</p>{community.map((item) => <div className="mcp-choice" key={item.name}><div><b>{item.name}</b><small>{item.description}</small><a href={item.url} target="_blank" rel="noreferrer">Project documentation</a></div><button type="button" className="ghost" disabled={busy} onClick={() => edit(item.config)}>Configure</button></div>)}</div>
    <div className="mcp-library"><strong>Add or edit a server</strong>
      <div className="setting-field"><label htmlFor="mcp-id">Server name</label><input id="mcp-id" value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} placeholder="my-server" /></div>
      <div className="setting-field"><label htmlFor="mcp-transport">Connection</label><select id="mcp-transport" value={draft.transport} onChange={(e) => setDraft({ ...draft, transport: e.target.value as McpConfig["transport"] })}><option value="stdio">Local process (stdio)</option><option value="http">Streamable HTTP</option></select></div>
      {draft.transport === "stdio" ? <><div className="setting-field"><label htmlFor="mcp-command">Executable</label><input id="mcp-command" value={draft.command} onChange={(e) => setDraft({ ...draft, command: e.target.value })} placeholder="uvx, npx or an installed executable" /></div><div className="setting-field"><label htmlFor="mcp-args">Arguments (JSON list)</label><textarea id="mcp-args" value={argumentsText} onChange={(e) => setArgumentsText(e.target.value)} spellCheck={false} /></div><div className="setting-field"><label htmlFor="mcp-env">Environment variable names (comma separated)</label><input id="mcp-env" value={environmentText} onChange={(e) => setEnvironmentText(e.target.value)} /><small>Values are read by the backend from its environment. Do not paste values here.</small></div></> : <div className="setting-field"><label htmlFor="mcp-url">Server URL</label><input id="mcp-url" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} placeholder="https://example.com/mcp" /><small>HTTPS required remotely. HTTP is allowed on loopback. OAuth-only servers are not supported yet.</small></div>}
      <div className="setting-field"><label htmlFor="mcp-timeout">Connection and tool timeout</label><select id="mcp-timeout" value={draft.timeout_s} onChange={(e) => setDraft({ ...draft, timeout_s: Number(e.target.value) })}>{![15, 30, 60, 120].includes(draft.timeout_s) && <option value={draft.timeout_s}>{draft.timeout_s} seconds</option>}{[15, 30, 60, 120].map((seconds) => <option value={seconds} key={seconds}>{seconds} seconds</option>)}</select><small>Allow more time for first startup when a package launcher needs to prepare a server.</small></div>
      <button type="button" className="ghost" disabled={busy || !draft.id.trim()} onClick={() => void save()}>{busy ? "Working..." : "Save server (disabled)"}</button>
    </div>
    {warnings.map((warning, index) => <p className="mcp-note" key={index}>{warning}</p>)}
    {error && <div className="settings-error" role="alert">{error}</div>}
  </section>;
}
