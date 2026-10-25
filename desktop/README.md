# AI Ecosystem Desktop (Gate 20 shell)

React + TypeScript + Tauri 2 shell over the local Python agent runtime.

- UI talks to `http://127.0.0.1:8765` (see `src/api.ts`).
- The UI never executes tools, decides permissions, or holds credentials.
- Python runtime (`src/ai_ecosystem/interface/`) is authoritative.

Run (requires Node 20+ and a Rust toolchain):

```sh
npm install
npm run dev     # Vite dev server for the webview
npm run tauri dev
```

## Model provider settings

Open **Settings** from the sidebar to configure the Python runtime endpoint and the Ollama provider. Use **Check** beside the Ollama endpoint to test connectivity and load installed model tags. The selected model and generation parameters are applied by the runtime to Ollama's native `/api/chat` API. Only Ollama is selectable in this version; LM Studio, llama.cpp, and hosted-provider fallback are not enabled by the desktop settings.

Defaults: `http://127.0.0.1:11434`, temperature `0.7`, top-p `0.9`, top-k `40`, repeat penalty `1.1`, context `8192`, and max output `1024` tokens.
