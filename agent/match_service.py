"""Persistent match sessions shared by the HTTP and CLI entry points."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError

from .exceptions import AgentRequestError
from .llm_adapter import ILlmAdapter
from .llm_types import ImagePart
from .match_graph import MatchWorkflow, timeline
from .match_types import MAX_MATCH_ROUNDS, MatchRoundInput
from .report import render_markdown
from .skill_loader import SkillLoader
from .tool_registry import ToolRegistry
from schemas import CoachResponse

_MATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


class MatchConflictError(AgentRequestError):
    pass


def check_match_id(match_id: str) -> str:
    if not isinstance(match_id, str) or not _MATCH_ID.fullmatch(match_id):
        raise AgentRequestError("match_id must contain 1-64 letters, digits, underscores or hyphens")
    return match_id


def _lock_for(database: Path, match_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        return _LOCKS.setdefault((str(database.resolve()), match_id), threading.Lock())


def _fingerprint(item: dict) -> str:
    # A file's display name is not its identity. Its content hash is in the media path.
    value = {**item, "images": [{"path": image["path"]} for image in item["images"]]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MatchService:
    def __init__(self, database: Path, media_dir: Path, registry: ToolRegistry | None = None):
        from tools.registry import build_registry

        self.database, self.media_dir = Path(database), Path(media_dir)
        self.registry = registry or build_registry(strict=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def prepare_rounds(self, match_id: str, raw_rounds: list[dict]) -> list[dict]:
        check_match_id(match_id)
        if not isinstance(raw_rounds, list) or len(raw_rounds) > MAX_MATCH_ROUNDS:
            raise AgentRequestError(f"rounds must be a list of at most {MAX_MATCH_ROUNDS} rounds")
        prepared = []
        for raw in raw_rounds:
            if not isinstance(raw, dict):
                raise AgentRequestError("each round must be a JSON object")
            item = dict(raw)
            raw_images = item.pop("images", [])
            if not isinstance(raw_images, list) or len(raw_images) > 10:
                raise AgentRequestError("each round accepts at most 10 images")
            # Validate the rest before writing any images; snapshot validity is a graph step.
            try:
                parsed = MatchRoundInput.model_validate({**item, "images": []})
            except ValidationError as exc:
                raise AgentRequestError(str(exc)) from exc
            references = []
            for image in raw_images:
                if not isinstance(image, dict) or not isinstance(image.get("data"), str):
                    raise AgentRequestError("round image must contain base64 data")
                try:
                    data = base64.b64decode(image["data"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise AgentRequestError("round image data is not valid base64") from exc
                decoded = ImagePart.from_bytes(data)
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                          "image/gif": ".gif"}[decoded.media_type]
                relative = f"{match_id}/{hashlib.sha256(data).hexdigest()}{suffix}"
                destination = self.media_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    destination.write_bytes(data)
                references.append({"path": relative, "name": str(image.get("name") or "opening" + suffix)[:250]})
            if parsed.snapshot is None and not references:
                raise AgentRequestError("each round requires a snapshot or at least one screenshot")
            prepared.append(parsed.model_copy(update={"images": references}).model_dump(mode="json"))
        indices = [item["round_index"] for item in prepared]
        if len(indices) != len(set(indices)):
            raise AgentRequestError("duplicate round_index in the same batch")
        return sorted(prepared, key=lambda item: item["round_index"])

    @staticmethod
    def _config(match_id: str) -> dict:
        return {"configurable": {"thread_id": match_id}, "recursion_limit": MAX_MATCH_ROUNDS * 12 + 20}

    def _workflow(self, llm: ILlmAdapter | None, saver: Any) -> MatchWorkflow:
        return MatchWorkflow(llm, self.registry, SkillLoader(), saver, self.media_dir)

    async def run(self, match_id: str, raw_rounds: list[dict], *, llm: ILlmAdapter | None = None,
                  text: str = "", observations: list[str] | None = None, summarize: bool = True,
                  resume: bool = False, replace_existing: bool = False) -> dict:
        check_match_id(match_id)
        if not isinstance(text, str) or len(text) > 20000:
            raise AgentRequestError("text must be a string of at most 20000 characters")
        if observations is not None and (not isinstance(observations, list)
                                        or any(not isinstance(value, str) for value in observations)):
            raise AgentRequestError("observations must be a list of strings")
        if not all(isinstance(value, bool) for value in (summarize, resume, replace_existing)):
            raise AgentRequestError("summarize, resume and replace_existing must be booleans")
        mode = "real" if llm is not None else "demo"
        lock = _lock_for(self.database, match_id)
        if not lock.acquire(blocking=False):
            raise MatchConflictError("this match is already being analyzed; check its progress")
        try:
            # State only contains JSON primitives. Reject custom objects on deserialization.
            async with AsyncSqliteSaver.from_conn_string(str(self.database)) as saver:
                saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
                graph = self._workflow(llm, saver).graph
                config = self._config(match_id)
                checkpoint = await graph.aget_state(config)
                old = checkpoint.values
                if old and old["mode"] != mode:
                    raise MatchConflictError("a match cannot mix offline demo and real model analysis; use a new match_id")
                if resume:
                    if raw_rounds:
                        raise AgentRequestError("resume accepts no new rounds")
                    if not old:
                        raise AgentRequestError("match does not exist")
                    if checkpoint.next:
                        await graph.ainvoke(None, config)
                else:
                    if checkpoint.next:
                        raise MatchConflictError("this match has an interrupted batch; resume it before uploading more rounds")
                    prepared = self.prepare_rounds(match_id, raw_rounds)
                    if mode == "demo" and any(not item["snapshot"] for item in prepared):
                        raise AgentRequestError("整场截图分析需要配置视觉模型；离线演示请提供逐回合快照 JSON。")
                    records = list(old.get("records", []))
                    by_index = {record["round_index"]: record for record in records}
                    new_inputs = {}
                    dirty = None
                    for item in prepared:
                        index = item["round_index"]
                        existing = by_index.get(index)
                        if existing:
                            identical = _fingerprint(existing["input"]) == _fingerprint(item)
                            if identical and existing["report"]["task_status"] != "failed":
                                continue
                            if not identical and not replace_existing:
                                raise MatchConflictError(f"round {index} already exists; use replace_existing to correct it")
                        new_inputs[index] = item
                        dirty = index if dirty is None else min(dirty, index)
                    if dirty is not None:
                        # Corrections and gap fills invalidate all later contextual reports.
                        for record in records:
                            if record["round_index"] >= dirty:
                                new_inputs.setdefault(record["round_index"], record["input"])
                        records = [record for record in records if record["round_index"] < dirty]
                    if len(records) + len(new_inputs) > MAX_MATCH_ROUNDS:
                        raise AgentRequestError(f"a match supports at most {MAX_MATCH_ROUNDS} rounds")
                    if not records and not new_inputs:
                        raise AgentRequestError("upload at least one round before requesting a summary")
                    await graph.ainvoke({"match_id": match_id, "mode": mode, "text": text or old.get("text", ""),
                        "observations": observations if observations is not None else old.get("observations", []),
                        "pending": [new_inputs[index] for index in sorted(new_inputs)], "cursor": 0,
                        "current": {}, "records": records, "summarize": summarize, "report": None,
                        "timeline": timeline(records), "trace": {}, "phase": "starting"}, config)
                checkpoint = await graph.aget_state(config)
                return self._public(checkpoint.values, bool(checkpoint.next), False)
        finally:
            lock.release()

    async def status(self, match_id: str) -> dict | None:
        check_match_id(match_id)
        async with AsyncSqliteSaver.from_conn_string(str(self.database)) as saver:
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
            graph = self._workflow(None, saver).graph
            checkpoint = await graph.aget_state(self._config(match_id))
            if not checkpoint.values:
                return None
            return self._public(checkpoint.values, bool(checkpoint.next), _lock_for(self.database, match_id).locked())

    @staticmethod
    def _public(state: dict, pending: bool, running: bool) -> dict:
        records = state.get("records", [])
        report = state.get("report")
        current = state.get("current", {}).get("input", {})
        return {"match_id": state["match_id"], "mode": state["mode"],
                "status": "running" if running else "interrupted" if pending else "ready",
                "phase": state.get("phase"), "can_resume": pending and not running,
                "completed_rounds": len(records), "pending_rounds": len(state.get("pending", [])) - state.get("cursor", 0),
                "current_round": current.get("round_index"),
                "next_round": max((record["round_index"] for record in records), default=0) + 1,
                "failed_rounds": [record["round_index"] for record in records if record["report"]["task_status"] == "failed"],
                "timeline": state.get("timeline", []),
                "rounds": [{"round_index": record["round_index"], "report": record["report"],
                            "round_snapshot": record.get("round_snapshot"),
                            "continuity": record.get("continuity", {})} for record in records],
                "report": report, "markdown": render_markdown(CoachResponse.model_validate(report)) if report else None,
                "trace": state.get("trace", {})}
