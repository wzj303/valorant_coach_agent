"""Pure converters between provider-neutral types and the OpenAI / Anthropic wire formats.

No network and no SDK imports here, so the logic is unit-testable offline.
"""
from __future__ import annotations

import json
from typing import Any

from .exceptions import LlmResponseError
from .llm_types import LlmRequest, LlmResponse, Message, TextPart, ToolCall, Usage

_FINISH = {"stop": "stop", "end_turn": "stop", "stop_sequence": "stop",
           "tool_calls": "tool_calls", "function_call": "tool_calls", "tool_use": "tool_calls",
           "length": "length", "max_tokens": "length"}


def schema_instruction(schema: dict[str, Any]) -> str:
    return ("Your final reply must be exactly one JSON object that conforms to the JSON Schema below. "
            "Do not wrap it in a code fence and do not add any other text.\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


def system_text(request: LlmRequest) -> str:
    parts = [m.text for m in request.messages if m.role == "system" and m.text]
    if request.response_schema:
        parts.append(schema_instruction(request.response_schema))
    return "\n\n".join(parts)


def _call_arguments(call: ToolCall) -> str:
    if call.parse_error and call.raw_arguments is not None:
        return call.raw_arguments
    return json.dumps(call.arguments, ensure_ascii=False)


def make_tool_call(index: int, call_id: Any, name: Any, arguments: Any) -> ToolCall:
    """Build a ToolCall. Malformed arguments are kept, not raised, so the agent can recover."""
    cid = str(call_id) if call_id else f"call_{index}"
    name = str(name or "")
    if isinstance(arguments, dict):
        return ToolCall(id=cid, name=name, arguments=arguments)
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    if not raw.strip():
        return ToolCall(id=cid, name=name)
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        return ToolCall(id=cid, name=name, parse_error=f"arguments are not valid JSON: {exc}",
                        raw_arguments=raw)
    if not isinstance(parsed, dict):
        return ToolCall(id=cid, name=name, parse_error="arguments must be a JSON object",
                        raw_arguments=raw)
    return ToolCall(id=cid, name=name, arguments=parsed)


# --------------------------------------------------------------------- OpenAI
def _openai_user_content(m: Message) -> str | list[dict[str, Any]]:
    if not m.images:
        return m.text
    out: list[dict[str, Any]] = []
    for p in m.content:
        if isinstance(p, TextPart):
            out.append({"type": "text", "text": p.text})
        else:
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{p.media_type};base64,{p.data}"}})
    return out


def to_openai(request: LlmRequest, *, model: str, max_tokens: int, json_mode: bool,
              max_tokens_field: str = "max_tokens") -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = system_text(request)
    if system:
        messages.append({"role": "system", "content": system})
    for m in request.messages:
        if m.role == "user":
            messages.append({"role": "user", "content": _openai_user_content(m)})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": _call_arguments(c)}}
                    for c in m.tool_calls]
            messages.append(entry)
        elif m.role == "tool":
            messages.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text})
    payload: dict[str, Any] = {"model": model, "messages": messages, max_tokens_field: max_tokens}
    if request.tools:
        payload["tools"] = [{"type": "function",
                             "function": {"name": t.name, "description": t.description,
                                          "parameters": t.parameters}} for t in request.tools]
        payload["tool_choice"] = request.tool_choice
    # JSON mode conflicts with tool calling on many gateways, so only use it on tool-less turns.
    if request.response_schema and json_mode and not request.tools:
        payload["response_format"] = {"type": "json_object"}
    return payload


def parse_openai(data: dict[str, Any]) -> LlmResponse:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise LlmResponseError("OpenAI-format response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    calls = []
    for i, c in enumerate(message.get("tool_calls") or []):
        fn = c.get("function") or {}
        calls.append(make_tool_call(i, c.get("id"), fn.get("name"), fn.get("arguments")))
    usage = data.get("usage") or {}
    finish = "tool_calls" if calls else _FINISH.get(choice.get("finish_reason") or "stop", "other")
    return LlmResponse(text=content or "", tool_calls=calls, finish_reason=finish,
                       model=data.get("model"), raw=data,
                       usage=Usage(input_tokens=usage.get("prompt_tokens") or 0,
                                   output_tokens=usage.get("completion_tokens") or 0))


# ------------------------------------------------------------------ Anthropic
def _anthropic_blocks(m: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for p in m.content:
        if isinstance(p, TextPart):
            blocks.append({"type": "text", "text": p.text})
        else:
            blocks.append({"type": "image", "source": {"type": "base64",
                                                       "media_type": p.media_type, "data": p.data}})
    return blocks


def to_anthropic(request: LlmRequest, *, model: str, max_tokens: int) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:  # the API requires alternating roles
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for m in request.messages:
        if m.role == "user":
            add("user", _anthropic_blocks(m))
        elif m.role == "assistant":
            blocks = _anthropic_blocks(m)
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                       for c in m.tool_calls]
            add("assistant", blocks)
        elif m.role == "tool":
            # Consecutive tool results merge into one user turn, ahead of any later user text.
            add("user", [{"type": "tool_result", "tool_use_id": m.tool_call_id,
                          "content": m.text, "is_error": m.is_error}])
    payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": turns}
    system = system_text(request)
    if system:
        payload["system"] = system
    if request.tools:
        payload["tools"] = [{"name": t.name, "description": t.description,
                             "input_schema": t.parameters} for t in request.tools]
        payload["tool_choice"] = {"type": request.tool_choice}
    return payload


def parse_anthropic(data: dict[str, Any]) -> LlmResponse:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise LlmResponseError("Anthropic-format response has no content list")
    blocks = [b for b in blocks if isinstance(b, dict)]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    uses = [b for b in blocks if b.get("type") == "tool_use"]
    calls = [make_tool_call(i, b.get("id"), b.get("name"), b.get("input") or {})
             for i, b in enumerate(uses)]
    usage = data.get("usage") or {}
    finish = "tool_calls" if calls else _FINISH.get(data.get("stop_reason") or "stop", "other")
    return LlmResponse(text=text, tool_calls=calls, finish_reason=finish, model=data.get("model"),
                       raw=data, usage=Usage(input_tokens=usage.get("input_tokens") or 0,
                                             output_tokens=usage.get("output_tokens") or 0))
