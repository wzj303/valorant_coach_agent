"""Provider-neutral message, tool and response types for the LLM layer."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .exceptions import InvalidImageError

DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def sniff_image_type(data: bytes) -> str | None:
    """Return the media type from magic bytes. File extensions are not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """An image sent to the model as real pixels (base64), never as a path string."""

    type: Literal["image"] = "image"
    media_type: str
    data: str = Field(repr=False)  # base64
    size_bytes: int
    label: str | None = None

    @classmethod
    def from_bytes(cls, raw: bytes, *, label: str | None = None,
                   max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> "ImagePart":
        where = f" ({label})" if label else ""
        if not raw:
            raise InvalidImageError(f"image is empty{where}")
        media_type = sniff_image_type(raw)
        if media_type is None:
            raise InvalidImageError(f"unsupported image format{where}; use PNG, JPEG, WebP or GIF")
        if len(raw) > max_bytes:
            raise InvalidImageError(f"image is {len(raw)} bytes, limit is {max_bytes}{where}")
        return cls(media_type=media_type, data=base64.b64encode(raw).decode("ascii"),
                   size_bytes=len(raw), label=label)

    @classmethod
    def from_file(cls, path: str | Path, *, max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> "ImagePart":
        p = Path(path)
        try:
            raw = p.read_bytes()
        except OSError as exc:
            raise InvalidImageError(f"cannot read image {p}: {exc.strerror or exc}") from exc
        return cls.from_bytes(raw, label=p.name, max_bytes=max_bytes)


ContentPart = TextPart | ImagePart


class ToolSpec(BaseModel):
    """A tool as the model sees it: name, description and a JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set when the provider sent arguments that are not a JSON object.
    parse_error: str | None = None
    raw_arguments: str | None = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentPart] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only
    tool_name: str | None = None  # tool only
    is_error: bool = False  # tool only

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.content if isinstance(p, TextPart))

    @property
    def images(self) -> list[ImagePart]:
        return [p for p in self.content if isinstance(p, ImagePart)]

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role="system", content=[TextPart(text=text)])

    @classmethod
    def user(cls, text: str, images: list[ImagePart] | None = None) -> "Message":
        parts: list[ContentPart] = [TextPart(text=text)] if text else []
        parts.extend(images or [])
        return cls(role="user", content=parts)

    @classmethod
    def assistant(cls, text: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=[TextPart(text=text)] if text else [],
                   tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, call: ToolCall, text: str, *, is_error: bool = False) -> "Message":
        return cls(role="tool", content=[TextPart(text=text)], tool_call_id=call.id,
                   tool_name=call.name, is_error=is_error)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class LlmRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    # "auto": the model may call tools. "none": it must answer in text.
    tool_choice: Literal["auto", "none"] = "auto"
    # A JSON Schema. When set, the final answer must be one JSON object matching it.
    response_schema: dict[str, Any] | None = None
    max_tokens: int | None = None
    timeout_s: float | None = None

    @property
    def has_images(self) -> bool:
        return any(m.images for m in self.messages)


class LlmResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "other"] = "stop"
    usage: Usage = Field(default_factory=Usage)
    model: str | None = None
    raw: dict[str, Any] | None = Field(default=None, repr=False)
