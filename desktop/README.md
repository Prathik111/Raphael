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
