# AI Ecosystem Desktop (Gate 20 shell)

React + TypeScript + Tauri 2 shell over the local Python agent runtime.

- UI talks to `http://127.0.0.1:8765` (see `src/api.ts`).
- The UI never executes tools, decides permissions, or persists credentials.
- Python runtime (`src/ai_ecosystem/interface/`) is authoritative.

Run (requires Node 20+ and a Rust toolchain):

```sh
npm install
npm run dev     # Vite dev server for the webview
npm run tauri dev
```

## Model provider settings

Open **Settings** from the sidebar to configure the Python runtime endpoint and the Ollama provider. Use **Test connection** beside the Ollama endpoint to check availability, or **Refresh** to reload installed model tags. Failed checks display an error; Save also verifies connectivity before applying settings. The selected model and generation parameters are applied by the runtime to Ollama's native `/api/chat` API. Only Ollama is selectable in this version; LM Studio, llama.cpp, and hosted-provider fallback are not enabled by the desktop settings.

Defaults: `http://127.0.0.1:11434`, temperature `0.7`, top-p `0.9`, top-k `40`, repeat penalty `1.1`, context `8192`, and max output `1024` tokens.

The current provider accepts only localhost or loopback IP endpoints. LAN and hosted providers are not selectable. Model discovery runs automatically at startup and when the Ollama endpoint changes. Changes take effect after **Save settings**.

## Backend startup from source

Install the project environment from the repository root with `uv sync` (or create `.venv` and install with `.venv/Scripts/python.exe -m pip install -e .`). The desktop prefers this environment before trying system Python commands, so activating it in the launching terminal is unnecessary. Restart the desktop after rebuilding for launcher changes to take effect. Startup failures show the log location (`%APPDATA%/AI Ecosystem/backend.log` on Windows). Installed builds still need an available Python runtime and dependencies; this change does not bundle Python.

## Slow Ollama responses

Settings includes **Request timeout (seconds)** (default 180, maximum 600) and **Thinking** (off by default). Raise the timeout for slow model loads; lower context/output length if generation is too slow. A successful connection test checks discovery, not inference speed. Timeout errors do not mean Ollama has stopped processing the request. Desktop result polling waits up to 15 minutes, then instructs you to check the conversation again without cancelling it.

## MCP tools

Open **Settings → MCP tools** to discover existing server configurations, choose a
community preset, or enter a local command/Streamable HTTP endpoint. Save, review,
then enable a server to discover its tools. Local commands run with your account's
access; package launchers may download software. Calls appear in an **Approve once /
Deny** panel, and successful external text results appear in chat. Discovery alone
never executes a server. OAuth-only hosted servers are not yet supported.

Run `uv sync` from the repository root and restart the backend/desktop after updating
to load the MCP SDK and new API routes. See [MCP documentation](../docs/MCP.md) for
configuration locations, supported schema limits, and the OS sandbox limitation.
