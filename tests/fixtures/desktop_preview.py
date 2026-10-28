"""Isolated desktop visual-check API; no model calls or user runtime data."""

import tempfile
import threading
from pathlib import Path

from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface import RuntimeAPI, LocalHttpServer
from ai_ecosystem.intelligence.models.ollama import OllamaSettings
from ai_ecosystem.tools.mcp.manager import MCPManager
from ai_ecosystem.tools.registry.registry import ToolRegistry


class PreviewAPI(RuntimeAPI):
    def list_tasks(self):
        return [
            {
                "task_id": "preview",
                "title": "Layout preview",
                "state": "COMPLETED",
                "completed": True,
                "created_at": "2026-10-28T10:00:00Z",
            }
        ]

    def get_task_result(self, task_id):
        return {
            "task_id": task_id,
            "status": "COMPLETED",
            "summary": "",
            "reply": "Responses now share the same centered conversation column as the message input.\n\nMCP servers and discovered tools are available in Settings.",
            "error": "",
        }

    def get_model_settings(self):
        return OllamaSettings(model="preview").model_dump()

    def update_model_settings(self, settings):
        return OllamaSettings.model_validate(settings).model_dump()

    def check_ollama(self, endpoint="http://127.0.0.1:11434"):
        return {
            "provider": "ollama",
            "endpoint": endpoint,
            "connected": True,
            "models": ["preview"],
        }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="raphael-visual-") as folder:
        runtime = AgentRuntime(str(Path(folder) / "preview.db"))
        manager = MCPManager(ToolRegistry(), runtime.db, folder)
        api = PreviewAPI(runtime, mcp=manager)
        server = LocalHttpServer(api).start()
        print(server.url, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
            manager.close()
            runtime.shutdown()
