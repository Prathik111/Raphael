## Update — 2026-10-25: Ollama provider configuration

The `feat/ollama-provider-settings` branch adds a single Ollama provider path to the desktop Settings page and runtime API. The desktop can check a local/private Ollama endpoint, list installed model tags, select a model, and save temperature, top-p, top-k, repeat penalty, context length, and output-token limit. The runtime applies these settings to Ollama's native `/api/chat` request and no longer automatically selects LM Studio or llama.cpp as fallback providers. Ollama settings are validated server-side; plain HTTP endpoints must use loopback/private IP addresses.

A regression test module was added at `tests/unit/test_ollama_provider.py`. Test execution and the desktop production build must still be run against this branch before treating the changes as verified.

