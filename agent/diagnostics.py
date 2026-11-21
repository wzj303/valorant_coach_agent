"""Check what a configured model can actually do, instead of assuming it.

`python -m agent --check-llm` runs three probes through the real adapter: plain text, an image
(a solid-colour PNG the model must name), and a tool call. Many OpenAI-compatible gateways
accept `image_url` parts and silently ignore them, so the image probe checks the answer.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .llm_adapter import ILlmAdapter
from .llm_types import ImagePart, LlmRequest, Message, ToolSpec


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


@dataclass
class Probe:
    name: str
    passed: bool
    detail: str


_ECHO = ToolSpec(name="record_color", description="Record the colour you see in the image.",
                 parameters={"type": "object", "required": ["color"], "additionalProperties": False,
                             "properties": {"color": {"type": "string"}}})
_COLOURS = {"red": ("red", "红"), "blue": ("blue", "蓝")}


async def _try(name: str, coro) -> Probe:
    try:
        return await coro
    except Exception as exc:  # a probe reports its failure, it does not raise
        return Probe(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")


async def check_capabilities(llm: ILlmAdapter) -> list[Probe]:
    async def text() -> Probe:
        r = await llm.chat(LlmRequest(messages=[Message.user("Reply with the single word: pong")], max_tokens=32))
        return Probe("text", bool(r.text.strip()), r.text.strip()[:60] or "empty reply")

    async def vision() -> Probe:
        if not llm.supports_vision:
            return Probe("vision", False, "disabled in config (VALORANT_LLM_VISION=0)")
        img = ImagePart.from_bytes(solid_png((0, 0, 255)), label="probe.png")
        r = await llm.chat(LlmRequest(max_tokens=32, messages=[
            Message.user("This image is one solid colour. Which colour: red, green or blue? One word.", [img])]))
        ok = any(w in r.text.lower() for w in _COLOURS["blue"])
        return Probe("vision", ok, f"answered {r.text.strip()[:40]!r}, expected blue"
                     + ("" if ok else " (the gateway may be dropping images)"))

    async def tools() -> Probe:
        r = await llm.chat(LlmRequest(tools=[_ECHO], max_tokens=128, messages=[
            Message.user("Call the record_color tool with color set to green.")]))
        ok = bool(r.tool_calls) and r.tool_calls[0].name == "record_color" and not r.tool_calls[0].parse_error
        return Probe("tool_calling", ok, f"tool_calls={[c.name for c in r.tool_calls]}" if r.tool_calls
                     else f"no tool call, text={r.text.strip()[:40]!r}")

    return [await _try("text", text()), await _try("vision", vision()), await _try("tool_calling", tools())]
