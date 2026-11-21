"""Zero-dependency HTTP frontend for the VALORANT AI Coach.

Serves the static web UI and a small JSON API:

    GET  /               web UI (static/index.html)
    GET  /api/health     server mode and available skills
    GET  /api/samples    sample RoundSnapshot / PositionSnapshot fixtures
    POST /api/analyze    run one coach analysis and return the report

The server uses only the Python standard library, so teammates need nothing
installed: one person starts `python -m webapp`, everyone on the LAN opens the
printed address in a browser. Without LLM credentials the offline scripted
model (DemoLlm) is used; with a configured .env the real model adapter is used.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import (AgentLoop, AgentRequest, AgentRequestError, AgentError, ImagePart,
                   InvalidImageError, LlmConfigError, SkillLoader)
from agent.demo import DemoLlm
from agent.llm_adapter import LlmConfig, RealLlmAdapter
from agent.report import render_markdown
from tools.registry import build_registry

log = logging.getLogger("webapp")

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "webapp" / "static"
FIXTURES_DIR = ROOT / "tests" / "schemas" / "fixtures"

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_IMAGES = 10
MAX_TEXT_CHARS = 20000
TASKS = ("buy_review", "position_review", "match_review")
_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                  ".png": "image/png", ".ico": "image/x-icon"}


def available_config() -> LlmConfig | None:
    try:
        return LlmConfig.from_env()
    except LlmConfigError:
        return None


def server_mode() -> str:
    return "real" if available_config() else "demo"


def lan_addresses() -> list[str]:
    addresses = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(addresses)


class Handler(BaseHTTPRequestHandler):
    server_version = "ValorantCoach/1.0"
    max_body = MAX_BODY_BYTES

    # ------------------------------------------------------------- plumbing
    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.max_body:
            self._error(413, f"request body exceeds the {self.max_body} byte limit")
            return b""
        if length <= 0:
            return b""
        body = self.rfile.read(length)
        if len(body) != length:
            raise ConnectionError("client disconnected mid-request")
        return body

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif self.path == "/api/health":
            self._health()
        elif self.path == "/api/samples":
            self._samples()
        elif self.path.startswith("/static/"):
            self._serve_static(self.path[len("/static/"):])
        else:
            self._error(404, "not found")

    def _serve_static(self, name: str) -> None:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
            self._error(404, "not found")
            return
        if not path.is_file():
            self._error(404, "not found")
            return
        kind = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self._send(200, kind, path.read_bytes())

    def _health(self) -> None:
        skills = SkillLoader()
        self._json(200, {
            "status": "ok",
            "mode": server_mode(),
            "tasks": list(TASKS),
            "skills": [{"name": name, "description": description}
                       for name, description in skills.catalog()],
        })

    def _samples(self) -> None:
        samples = {}
        for name in ("complete_round", "partial_round", "position_snapshot", "invalid_round"):
            path = FIXTURES_DIR / f"{name}.json"
            if path.is_file():
                samples[name] = json.loads(path.read_text(encoding="utf-8"))
        if not samples:
            self._error(404, "sample fixtures not found")
            return
        self._json(200, {"samples": samples})

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        if self.path != "/api/analyze":
            self._error(404, "not found")
            return
        try:
            body = self._read_body()
        except (ConnectionError, TimeoutError) as exc:
            log.warning("read failed: %s", exc)
            return
        if not body:
            return  # _read_body already answered 413
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "body must be a UTF-8 JSON object")
            return
        if not isinstance(payload, dict):
            self._error(400, "body must be a JSON object")
            return
        self._analyze(payload)

    def _analyze(self, payload: dict) -> None:
        request, force_demo, max_iterations = self._build_request(payload)
        if request is None:
            return  # _build_request already answered with 400

        config = None if force_demo else available_config()
        mode = "real" if config is not None else "demo"
        try:
            if mode == "real":
                llm = RealLlmAdapter(config)
            else:
                llm = DemoLlm(request.task_id, request.task_type,
                              [payload["snapshot"]] if payload.get("snapshot") else None)
            registry = build_registry()
            skills = SkillLoader()
            skills.validate_tools(registry.names())
            loop = AgentLoop(llm, registry, skills, max_iterations=max_iterations)

            async def run() -> Any:
                try:
                    return await loop.run_with_trace(request)
                finally:
                    await llm.aclose()

            result = asyncio.run(run())
        except (AgentRequestError, InvalidImageError) as exc:
            self._error(400, str(exc))
            return
        except AgentError as exc:
            log.exception("analysis failed")
            self._error(500, str(exc))
            return
        except Exception as exc:  # server bug: never leak a traceback to the browser
            log.exception("unexpected server error")
            self._error(500, f"unexpected server error: {type(exc).__name__}")
            return
        self._json(200, {
            "mode": mode,
            "report": result.response.model_dump(mode="json"),
            "markdown": render_markdown(result.response),
            "trace": result.trace.model_dump(mode="json"),
        })

    def _build_request(self, payload: dict) -> tuple[AgentRequest | None, bool, int]:
        task = payload.get("task_type")
        if task is not None and task not in TASKS:
            self._error(400, f"task_type must be one of {', '.join(TASKS)} or null")
            return None, False, 0
        text = payload.get("text")
        if text is None:
            text = ""
        if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
            self._error(400, f"text must be a string of at most {MAX_TEXT_CHARS} characters")
            return None, False, 0

        observations = payload.get("observations") or []
        if not isinstance(observations, list) or any(not isinstance(o, str) for o in observations):
            self._error(400, "observations must be a list of strings")
            return None, False, 0

        images = []
        raw_images = payload.get("images") or []
        if not isinstance(raw_images, list) or len(raw_images) > MAX_IMAGES:
            self._error(400, f"images must be a list of at most {MAX_IMAGES} items")
            return None, False, 0
        for index, item in enumerate(raw_images, 1):
            if not isinstance(item, dict) or not isinstance(item.get("data"), str):
                self._error(400, f"image {index} must be an object with a base64 'data' string")
                return None, False, 0
            try:
                raw = base64.b64decode(item["data"], validate=True)
            except (binascii.Error, ValueError):
                self._error(400, f"image {index}: data is not valid base64")
                return None, False, 0
            try:
                images.append(ImagePart.from_bytes(raw, label=str(item.get("name") or f"image-{index}")))
            except InvalidImageError as exc:
                self._error(400, f"image {index}: {exc}")
                return None, False, 0

        snapshot = payload.get("snapshot")
        if snapshot is not None and not isinstance(snapshot, dict):
            self._error(400, "snapshot must be a JSON object or null")
            return None, False, 0

        max_iterations = payload.get("max_iterations")
        if max_iterations is None:
            max_iterations = 12
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) \
                or not 1 <= max_iterations <= 24:
            self._error(400, "max_iterations must be an integer between 1 and 24")
            return None, False, 0

        request = AgentRequest(text=text.strip(), images=images, task_type=task,
                               user_observations=[o.strip() for o in observations if o.strip()])
        return request, bool(payload.get("force_demo")), max_iterations


def make_handler(max_body_bytes: int = MAX_BODY_BYTES) -> type[Handler]:
    return type("ConfiguredHandler", (Handler,), {"max_body": max_body_bytes})


def start_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT, *, quiet: bool = False,
                 max_body_bytes: int = MAX_BODY_BYTES):
    """Start the HTTP server in a daemon thread. Returns (server, port, thread)."""
    server = ThreadingHTTPServer((host, port), make_handler(max_body_bytes))
    server.daemon_threads = True
    bound_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="valorant-webapp", daemon=True)
    thread.start()
    if not quiet:
        mode = server_mode()
        mode_text = "真实模型" if mode == "real" else "离线演示（未配置 .env 模型凭据）"
        print(f"VALORANT 教练 Web 界面已启动（模式：{mode_text}）")
        print(f"  本机地址: http://127.0.0.1:{bound_port}/")
        for address in lan_addresses():
            if address != "127.0.0.1":
                print(f"  局域网地址: http://{address}:{bound_port}/  （队友可通过此地址访问）")
        print("按 Ctrl+C 停止服务。")
    return server, bound_port, thread
