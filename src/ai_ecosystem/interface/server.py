"""Local HTTP transport for RuntimeAPI with fail-closed remote auth."""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai_ecosystem.core.errors.exceptions import AiEcosystemError, ResourceNotFoundError
from ai_ecosystem.interface.api import ApiError, RuntimeAPI

MAX_BODY_BYTES = 1_000_000
ALLOWED_ORIGINS = frozenset(
    {"http://tauri.localhost", "tauri://localhost", "http://localhost:1420"}
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ApiUnavailableError(AiEcosystemError):
    """The runtime could not be reached."""


def _error_code(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, ApiError):
        table = {
            "malformed_request": 400,
            "invalid_transition": 409,
            "unsupported": 501,
            "unavailable": 503,
            "unauthorized": 401,
            "forbidden": 403,
            "queue_full": 429,
        }
        return table.get(exc.code, 400), exc.code
    if isinstance(exc, ResourceNotFoundError):
        return 404, "not_found"
    return 500, "internal_error"


def _authorized(headers: Any, auth_token: str | None) -> bool:
    if not auth_token:
        return True  # Safe only because LocalHttpServer forbids unauthenticated remote binds.
    presented = headers.get("Authorization") or ""
    return hmac.compare_digest(presented, f"Bearer {auth_token}")


def _cors_origin(headers: Any) -> str | None:
    origin = headers.get("Origin")
    return origin if origin in ALLOWED_ORIGINS else None


class _Handler(BaseHTTPRequestHandler):
    api: RuntimeAPI
    auth_token: str | None = None
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        origin = _cors_origin(self.headers)
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        if self.command.upper() != "OPTIONS":
            self.wfile.write(body)
        self.close_connection = True

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("malformed_request", "invalid Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise ApiError("malformed_request", "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("malformed_request", f"invalid JSON: {exc}") from exc

    def _route(self) -> None:
        if self.command.upper() == "OPTIONS":
            self._send(204, {})
            return
        if not _authorized(self.headers, self.auth_token):
            self._send(401, {"code": "unauthorized", "message": "missing or invalid bearer token"})
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)
        method = self.command.upper()
        try:
            if method == "GET" and parts == ["health"]:
                self._send(200, {"status": "ok"})
            elif method == "POST" and parts == ["tasks"]:
                body = self._read_json()
                goal = body.get("goal") if isinstance(body, dict) else None
                self._send(201, self.api.create_task(goal))
            elif method == "GET" and parts == ["tasks"]:
                self._send(200, self.api.list_tasks())
            elif method == "GET" and len(parts) == 2 and parts[0] == "tasks":
                self._send(200, self.api.get_task(parts[1]))
            elif (
                method == "GET" and len(parts) == 3 and parts[0] == "tasks" and parts[2] == "status"
            ):
                self._send(200, self.api.get_task_status(parts[1]))
            elif (
                method == "GET" and len(parts) == 3 and parts[0] == "tasks" and parts[2] == "result"
            ):
                self._send(200, self.api.get_task_result(parts[1]))
            elif (
                method == "POST"
                and len(parts) == 3
                and parts[0] == "tasks"
                and parts[2] == "cancel"
            ):
                self._send(200, self.api.cancel_task(parts[1]))
            elif (
                method == "GET" and len(parts) == 3 and parts[0] == "tasks" and parts[2] == "events"
            ):
                try:
                    since = int(query.get("since", ["0"])[0] or 0)
                except (TypeError, ValueError):
                    raise ApiError("malformed_request", "'since' must be an integer") from None
                if since < 0:
                    raise ApiError("malformed_request", "'since' must be >= 0")
                self._send(200, self.api.get_task_events(parts[1], since))
            elif method == "GET" and parts == ["agents", "status"]:
                self._send(200, self.api.get_agent_status())
            elif method == "GET" and parts == ["awareness"]:
                self._send(200, self.api.get_system_awareness())
            elif method == "GET" and parts == ["models"]:
                self._send(200, self.api.get_models())
            elif method == "GET" and parts == ["skills"]:
                self._send(200, self.api.get_skills())
            elif method == "GET" and parts == ["approvals"]:
                self._send(
                    200, self.api.list_approvals(query.get("status", ["PENDING"])[0] or "PENDING")
                )
            elif (
                method == "POST"
                and len(parts) == 3
                and parts[0] == "approvals"
                and parts[2] in ("approve", "deny")
            ):
                self._read_json()
                self._send(
                    200,
                    self.api.decide_approval(
                        parts[1], parts[2] == "approve", decided_by="authenticated-operator"
                    ),
                )
            else:
                self._send(404, {"code": "not_found", "message": "unknown route"})
        except Exception as exc:
            status, code = _error_code(exc)
            if status >= 500:
                import logging

                logging.getLogger("ai_ecosystem.server").warning(
                    "internal error on %s: %s", self.path, exc
                )
                message = "internal error"
            else:
                message = str(exc)
            self._send(status, {"code": code, "message": message})

    do_GET = _route
    do_POST = _route
    do_OPTIONS = _route


class LocalHttpServer:
    """Threaded HTTP server; unauthenticated mode is loopback-only."""

    def __init__(
        self,
        api: RuntimeAPI,
        host: str = "127.0.0.1",
        port: int = 0,
        allow_remote: bool = False,
        auth_token: str | None = None,
    ) -> None:
        if not allow_remote and host not in LOOPBACK_HOSTS:
            raise ApiError(
                "malformed_request",
                f"refusing non-loopback bind {host!r} without allow_remote=True",
            )
        if host not in LOOPBACK_HOSTS and not auth_token:
            raise ApiError("unauthorized", "remote API exposure requires authentication")
        handler = type("BoundHandler", (_Handler,), {"api": api, "auth_token": auth_token})
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> LocalHttpServer:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


class ApiClient:
    def __init__(
        self, base_url: str, timeout_s: float = 5.0, auth_token: str | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._auth_token = auth_token

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        request = urllib.request.Request(
            self._base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(detail)
                raise ApiError(
                    payload.get("code", "http_error"), payload.get("message", detail)
                ) from exc
            except (ValueError, AttributeError):
                raise ApiError("http_error", f"{exc.code}: {detail}") from exc
        except OSError as exc:
            raise ApiUnavailableError(f"runtime unreachable: {exc}") from exc

    def health(self) -> Any:
        return self._call("GET", "/health")

    def submit(self, goal: str) -> Any:
        return self._call("POST", "/tasks", {"goal": goal})

    def get(self, path: str) -> Any:
        return self._call("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self._call("POST", path, body)
