"""Small helpers shared by the registry and the agent loop."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n```", re.S)


def compact_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the JSON object in a model reply: bare, code-fenced, or followed by prose."""
    for candidate in [text.strip(), *(m.strip() for m in _FENCE.findall(text))]:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    start = text.find("{")
    if start >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            return None
        if isinstance(value, dict):
            return value
    return None


def strip_titles(schema: Any) -> Any:
    """Drop pydantic's auto-generated 'title' keys. Property names called 'title' are kept."""
    if isinstance(schema, list):
        return [strip_titles(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: strip_titles(sub) for name, sub in value.items()}
        else:
            out[key] = strip_titles(value)
    return out


def format_validation_error(exc: ValidationError, limit: int = 12) -> list[str]:
    """One 'path: message' line per problem, with short scalar inputs shown, so a model can fix them."""
    lines: list[str] = []
    for issue in exc.errors(include_url=False):
        path = ".".join(str(p) for p in issue["loc"]) or "input"
        line = f"{path}: {issue['msg']}"
        got = issue.get("input")
        if isinstance(got, (str, int, float, bool)) and issue["type"] != "missing":
            line += f" (got {str(got)[:80]!r})"
        lines.append(line)
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more problems"]
    return lines
