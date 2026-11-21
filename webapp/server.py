"""Zero-dependency HTTP frontend for the VALORANT AI Coach.

Serves the static web UI and a small JSON API:

    GET  /                 web UI (static/index.html)
    GET  /api/health       server mode, skills and teammate addresses
    GET  /api/config       current model config (API key never returned)
    POST /api/config       save model config to the server-side .env
    POST /api/config/test  probe text / vision / tool-calling of a model config
    GET  /api/samples      sample RoundSnapshot / PositionSnapshot fixtures
    POST /api/analyze      run one coach analysis and return the report
    POST /api/matches/analyze   analyze/append full-match rounds, then optionally summarize
    GET  /api/matches/{id}      saved rounds and live graph progress
    POST /api/matches/{id}/resume  continue an interrupted checkpoint

The server uses only the Python standard library, so teammates need nothing
installed: one person starts `python -m webapp`, everyone on the LAN opens the
printed address in a browser. Without LLM credentials the offline scripted
model (DemoLlm) is used; the web UI can also save a model config to the local
.env file (shared by the whole team) or pass a per-request config that is used
for that one analysis only and never stored.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent import (AgentLoop, AgentRequest, AgentRequestError, AgentError, ImagePart,
                   InvalidImageError, LlmConfigError, SkillLoader)
from agent.demo import DemoLlm
from agent.diagnostics import check_capabilities
from agent.llm_adapter import LlmConfig, RealLlmAdapter
from agent.report import render_markdown
from agent.match_service import MatchConflictError, MatchService
from tools.registry import build_registry

log = logging.getLogger("webapp")

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "webapp" / "static"
FIXTURES_DIR = ROOT / "tests" / "schemas" / "fixtures"
ENV_FILE = ROOT / ".env"
MATCH_DB = ROOT / ".state" / "matches.sqlite"
MATCH_MEDIA = ROOT / ".state" / "match-images"

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_IMAGES = 10
MAX_TEXT_CHARS = 20000
TASKS = ("buy_review", "position_review", "match_review")
_ENV_KEYS = ("VALORANT_LLM_FORMAT", "VALORANT_LLM_BASE_URL", "VALORANT_LLM_API_KEY",
             "VALORANT_LLM_MODEL")
_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                  ".png": "image/png", ".ico": "image/x-icon"}


# ------------------------------------------------------------- .env handling
def _read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (with optional quotes). Missing file -> {}."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update only the managed VALORANT_LLM_* keys; keep every other line intact."""
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept_keys = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                lines[index] = f"{key}={json.dumps(updates[key], ensure_ascii=False)}"
                kept_keys.add(key)
    for key in sorted(updates):
        if key not in kept_keys:
            lines.append(f"{key}={json.dumps(updates[key], ensure_ascii=False)}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _merged_env() -> dict[str, str]:
    """Process env wins; .env file fills in anything unset (like python-dotenv)."""
    merged = dict(os.environ)
    for key, value in _read_env_file(ENV_FILE).items():
        if key not in merged:
            merged[key] = value
    return merged


def available_config() -> LlmConfig | None:
    try:
        return LlmConfig.from_env(_merged_env())
    except LlmConfigError:
        return None


def config_snapshot() -> dict[str, Any]:
    """Masked config for the UI. The API key itself is never sent back."""
    values = {key: _merged_env().get(key, "") for key in _ENV_KEYS}
    return {
        "format": values["VALORANT_LLM_FORMAT"] or "openai",
        "base_url": values["VALORANT_LLM_BASE_URL"] or "",
        "model": values["VALORANT_LLM_MODEL"] or "",
        "has_key": bool(values["VALORANT_LLM_API_KEY"]),
    }


def _config_from_fields(fields: dict, *, existing: dict[str, str] | None = None) -> LlmConfig:
    """Build a LlmConfig from user-supplied fields, falling back to saved values."""
    saved = existing if existing is not None else {key: _merged_env().get(key, "") for key in _ENV_KEYS}

    def pick(name: str, default: str) -> str:
        value = fields.get(name)
        return str(value).strip() if value not in (None, "") else default

    api_format = pick("format", saved["VALORANT_LLM_FORMAT"] or "openai")
    if api_format not in ("openai", "anthropic"):
        raise LlmConfigError("format must be 'openai' or 'anthropic'")
    api_key = pick("api_key", saved["VALORANT_LLM_API_KEY"])
    model = pick("model", saved["VALORANT_LLM_MODEL"])
    if not api_key or not model:
        raise LlmConfigError("api_key and model are required (save a server config first, "
                             "or fill in both fields)")
    try:
        return LlmConfig(api_format=api_format,
                         base_url=pick("base_url", saved["VALORANT_LLM_BASE_URL"]) or None,
                         api_key=api_key, model=model)
    except ValueError as exc:
        raise LlmConfigError(f"invalid model config: {exc}") from exc


def with_snapshot_text(text: str, snapshot: dict) -> str:
    """Append a pasted snapshot to the request text so a real model can use it."""
    note = ("\n\n用户提供了已确认的结构化快照 JSON（可原样用作 validate_round_data 等工具的"
            "参数，不需要重新识别）：\n")
    return (text + note + json.dumps(snapshot, ensure_ascii=False)).strip()


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

    def _read_json_body(self) -> dict | None:
        try:
            body = self._read_body()
        except (ConnectionError, TimeoutError) as exc:
            log.warning("read failed: %s", exc)
            return None
        if not body:
            return None  # _read_body already answered 413
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "body must be a UTF-8 JSON object")
            return None
        if not isinstance(payload, dict):
            self._error(400, "body must be a JSON object")
            return None
        return payload

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
        elif self.path == "/api/config":
            self._json(200, config_snapshot())
        elif self.path == "/api/samples":
            self._samples()
        elif self.path.startswith("/api/matches/"):
            self._match_status(self.path.removeprefix("/api/matches/"))
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
        port = self.server.server_address[1]
        self._json(200, {
            "status": "ok",
            "mode": server_mode(),
            "tasks": list(TASKS),
            "skills": [{"name": name, "description": description}
                       for name, description in skills.catalog()],
            "addresses": [f"http://{address}:{port}/" for address in lan_addresses()],
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
        if self.path == "/api/matches/analyze":
            payload = self._read_json_body()
            if payload is not None:
                self._analyze_match(payload)
        elif self.path.startswith("/api/matches/") and self.path.endswith("/resume"):
            payload = self._read_json_body()
            if payload is not None:
                self._analyze_match(payload, resume_id=self.path[len("/api/matches/"):-len("/resume")])
        elif self.path == "/api/analyze":
            payload = self._read_json_body()
            if payload is not None:
                self._analyze(payload)
        elif self.path == "/api/config":
            payload = self._read_json_body()
            if payload is not None:
                self._save_config(payload)
        elif self.path == "/api/config/test":
            payload = self._read_json_body()
            if payload is not None:
                self._test_config(payload)
        else:
            self._error(404, "not found")

    def _save_config(self, payload: dict) -> None:
        saved = {key: _merged_env().get(key, "") for key in _ENV_KEYS}
        try:
            config = _config_from_fields(payload, existing=saved)
        except LlmConfigError as exc:
            self._error(400, str(exc))
            return
        updates = {
            "VALORANT_LLM_FORMAT": config.api_format,
            "VALORANT_LLM_BASE_URL": config.base_url or "",
            "VALORANT_LLM_API_KEY": config.api_key,
            "VALORANT_LLM_MODEL": config.model,
        }
        try:
            _write_env_file(ENV_FILE, updates)
        except OSError as exc:
            log.exception("cannot write .env")
            self._error(500, f"cannot write config file: {exc}")
            return
        log.info("model config saved (format=%s model=%s)", config.api_format, config.model)
        self._json(200, config_snapshot())

    def _test_config(self, payload: dict) -> None:
        saved = {key: _merged_env().get(key, "") for key in _ENV_KEYS}
        try:
            config = _config_from_fields(payload, existing=saved)
        except LlmConfigError as exc:
            self._error(400, str(exc))
            return
        llm = RealLlmAdapter(config)

        async def run() -> Any:
            try:
                return await check_capabilities(llm)
            finally:
                await llm.aclose()

        try:
            probes = asyncio.run(run())
        except Exception as exc:  # adapter construction or probe plumbing failure
            log.exception("capability probe failed")
            self._error(500, f"capability probe failed: {type(exc).__name__}: {exc}")
            return
        self._json(200, {
            "probes": [{"name": probe.name, "passed": probe.passed, "detail": probe.detail}
                       for probe in probes],
            "all_passed": all(probe.passed for probe in probes),
        })

    def _match_status(self, match_id: str) -> None:
        try:
            result = asyncio.run(MatchService(MATCH_DB, MATCH_MEDIA).status(match_id))
        except AgentRequestError as exc:
            self._error(400, str(exc))
            return
        if result is None:
            self._error(404, "match not found")
        else:
            self._json(200, result)

    def _analyze_match(self, payload: dict, *, resume_id: str | None = None) -> None:
        match_id = resume_id or payload.get("match_id") or f"match-{uuid.uuid4().hex[:16]}"
        llm = None
        try:
            config_fields = payload.get("config")
            if config_fields is not None and not isinstance(config_fields, dict):
                raise AgentRequestError("config must be an object")
            config = (_config_from_fields(config_fields) if config_fields is not None else
                      None if payload.get("force_demo") else available_config())
            llm = RealLlmAdapter(config) if config is not None else None

            async def run() -> dict:
                try:
                    return await MatchService(MATCH_DB, MATCH_MEDIA).run(
                        match_id, payload.get("rounds", []), llm=llm, text=payload.get("text", ""),
                        observations=payload.get("observations"), summarize=payload.get("summarize", True),
                        resume=resume_id is not None, replace_existing=payload.get("replace_existing", False))
                finally:
                    if llm is not None:
                        await llm.aclose()

            result = asyncio.run(run())
        except MatchConflictError as exc:
            self._json(409, {"error": str(exc), "match_id": match_id})
            return
        except (AgentRequestError, InvalidImageError, LlmConfigError) as exc:
            self._json(400, {"error": str(exc), "match_id": match_id})
            return
        except Exception as exc:
            log.exception("match analysis interrupted")
            self._json(500, {"error": f"分析中断（{type(exc).__name__}），可通过比赛编号查询进度并继续。",
                             "match_id": match_id})
            return
        self._json(200, result)

    def _analyze(self, payload: dict) -> None:
        if payload.get("task_type") == "match_review" and (
                payload.get("images") or "rounds" in payload or payload.get("match_id")):
            if "rounds" not in payload:
                start = payload.get("start_round", 1)
                images = payload.get("images", [])
                if isinstance(start, bool) or not isinstance(start, int) or not isinstance(images, list):
                    self._error(400, "start_round must be an integer and images must be a list")
                    return
                payload = {**payload, "rounds": [{"round_index": start + index, "images": [image]}
                                                 for index, image in enumerate(images)]}
            self._analyze_match(payload)
            return
        request, force_demo, max_iterations = self._build_request(payload)
        if request is None:
            return  # _build_request already answered with 400

        request_config = payload.get("config")
        if isinstance(request_config, dict):
            try:
                config = _config_from_fields(request_config)
            except LlmConfigError as exc:
                self._error(400, str(exc))
                return
            mode = "real"
        elif force_demo:
            config, mode = None, "demo"
        else:
            config = available_config()
            mode = "real" if config is not None else "demo"
        try:
            if mode == "real":
                if payload.get("snapshot"):
                    request = AgentRequest(text=with_snapshot_text(request.text, payload["snapshot"]),
                                           images=request.images, task_type=request.task_type,
                                           user_observations=request.user_observations,
                                           task_id=request.task_id)
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
        mode_text = "真实模型" if mode == "real" else "离线演示（可在页面里配置模型）"
        print(f"VALORANT 教练 Web 界面已启动（模式：{mode_text}）")
        print(f"  本机地址: http://127.0.0.1:{bound_port}/")
        for address in lan_addresses():
            if address != "127.0.0.1":
                print(f"  局域网地址: http://{address}:{bound_port}/  （队友可通过此地址访问）")
        print("按 Ctrl+C 停止服务。")
    return server, bound_port, thread
