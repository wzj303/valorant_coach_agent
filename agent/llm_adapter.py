"""LLM adapter interface and the real, SDK-backed implementation.

Transport, retries and typed errors come from the official `openai` and `anthropic`
SDKs. This module only builds requests (see llm_convert) and maps SDK errors to ours.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from . import llm_convert
from .exceptions import (LlmCapabilityError, LlmConfigError, LlmProviderError, LlmResponseError,
                         LlmTimeoutError)
from .llm_types import LlmRequest, LlmResponse

ENV_PREFIX = "VALORANT_LLM_"


class ILlmAdapter(ABC):
    """One chat round trip. Must accept text, images and tool definitions."""

    #: False means the adapter cannot take images; AgentLoop then rejects image requests early.
    supports_vision: bool = True

    @abstractmethod
    async def chat(self, request: LlmRequest) -> LlmResponse:
        """Raise an LlmError subclass on failure. Never return a half-built response."""

    async def aclose(self) -> None:  # pragma: no cover - default is a no-op
        return None


class LlmConfig(BaseModel):
    api_format: Literal["openai", "anthropic"] = "openai"
    base_url: str | None = None
    api_key: str = Field(repr=False)
    model: str
    timeout_s: float = 120.0
    max_tokens: int = 4096
    max_retries: int = 2
    vision: bool = True
    json_mode: bool = False
    max_tokens_field: str = "max_tokens"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmConfig":
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = env.get(ENV_PREFIX + name)
            return value.strip() if value and value.strip() else None

        missing = [ENV_PREFIX + n for n in ("API_KEY", "MODEL") if not get(n)]
        if missing:
            raise LlmConfigError("missing environment variable(s): " + ", ".join(missing)
                                 + " (see .env.example)")

        def flag(name: str, default: bool) -> bool:
            raw = get(name)
            return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}

        try:
            return cls(api_format=get("FORMAT") or "openai", base_url=get("BASE_URL"),
                       api_key=get("API_KEY"), model=get("MODEL"),
                       timeout_s=float(get("TIMEOUT_S") or 120), max_tokens=int(get("MAX_TOKENS") or 4096),
                       max_retries=int(get("MAX_RETRIES") or 2), vision=flag("VISION", True),
                       json_mode=flag("JSON_MODE", False),
                       max_tokens_field=get("MAX_TOKENS_FIELD") or "max_tokens")
        except ValueError as exc:
            raise LlmConfigError(f"invalid LLM configuration: {exc}") from exc


class RealLlmAdapter(ILlmAdapter):
    """Talks to a vision + tool-calling model through the OpenAI or Anthropic SDK.

    Vision is a declared capability (`LlmConfig.vision`), never an assumption: a request with
    images is refused when it is off. Use agent.diagnostics.check_capabilities to verify a model.
    """

    def __init__(self, config: LlmConfig, client: Any | None = None) -> None:
        self.config = config
        self.supports_vision = config.vision
        self._client = client if client is not None else self._make_client(config)

    @staticmethod
    def _make_client(config: LlmConfig) -> Any:
        common = dict(api_key=config.api_key, base_url=config.base_url,
                      timeout=config.timeout_s, max_retries=config.max_retries)
        if config.api_format == "anthropic":
            import anthropic
            return anthropic.AsyncAnthropic(**common)
        import openai
        return openai.AsyncOpenAI(**common)

    async def chat(self, request: LlmRequest) -> LlmResponse:
        if request.has_images and not self.supports_vision:
            raise LlmCapabilityError(
                f"model {self.config.model!r} is configured without vision "
                f"({ENV_PREFIX}VISION=0) but the request contains images")
        max_tokens = request.max_tokens or self.config.max_tokens
        timeout = request.timeout_s or self.config.timeout_s
        try:
            if self.config.api_format == "anthropic":
                payload = llm_convert.to_anthropic(request, model=self.config.model, max_tokens=max_tokens)
                message = await self._client.messages.create(**payload, timeout=timeout)
                return llm_convert.parse_anthropic(_as_dict(message))
            payload = llm_convert.to_openai(request, model=self.config.model, max_tokens=max_tokens,
                                            json_mode=self.config.json_mode,
                                            max_tokens_field=self.config.max_tokens_field)
            completion = await self._client.chat.completions.create(**payload, timeout=timeout)
            return llm_convert.parse_openai(_as_dict(completion))
        except (LlmResponseError, LlmCapabilityError):
            raise
        except Exception as exc:  # SDK errors are translated; anything else is a bug and re-raised
            raise _translate_sdk_error(exc) from exc

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if dump is None:
        raise LlmResponseError(f"unexpected response object of type {type(obj).__name__}")
    return dump()


def _translate_sdk_error(exc: Exception) -> Exception:
    """Map SDK exceptions by class name so this works for both SDKs (and for gateways' subclasses)."""
    names = {c.__name__ for c in type(exc).__mro__}
    if "APITimeoutError" in names or isinstance(exc, TimeoutError):
        return LlmTimeoutError(f"LLM request timed out: {exc}")
    if "APIStatusError" in names:
        status = getattr(exc, "status_code", None)
        request = getattr(exc, "request", None)
        url = getattr(request, "url", None) if request is not None else None
        message = f"LLM provider returned HTTP {status}"
        if url:
            message += f" for {url}"
        message += f": {_short(exc)}"
        return LlmProviderError(message, status_code=status)
    if "APIConnectionError" in names:
        return LlmProviderError(f"cannot reach LLM provider: {_short(exc)}")
    return exc


def _short(exc: Exception, limit: int = 400) -> str:
    text = str(exc)
    return text if len(text) <= limit else text[:limit] + "..."
