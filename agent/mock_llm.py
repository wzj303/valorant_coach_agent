"""Scripted LLM for tests and offline demos. Never calls a network."""
from __future__ import annotations

from typing import Any, Callable, Union

from schemas import CoachResponse

from .exceptions import LlmCapabilityError, MockLlmExhausted
from .llm_adapter import ILlmAdapter
from .llm_types import LlmRequest, LlmResponse, ToolCall, Usage

Step = Union[LlmResponse, Exception, Callable[[LlmRequest], Union[LlmResponse, Exception]]]


def tool_call_response(*calls: tuple[str, dict[str, Any]], text: str = "") -> LlmResponse:
    """A turn in which the model asks for one or more tools: ("name", {args}), ..."""
    tool_calls = [ToolCall(id=f"call_{i + 1}", name=n, arguments=a) for i, (n, a) in enumerate(calls)]
    return LlmResponse(text=text, tool_calls=tool_calls, finish_reason="tool_calls",
                       usage=Usage(input_tokens=10, output_tokens=5))


def text_response(text: str, finish_reason: str = "stop") -> LlmResponse:
    return LlmResponse(text=text, finish_reason=finish_reason, usage=Usage(input_tokens=10, output_tokens=5))


def final_response(report: CoachResponse | dict[str, Any] | str) -> LlmResponse:
    """A final turn carrying a CoachResponse as JSON text (a str is passed through untouched)."""
    if isinstance(report, str):
        return text_response(report)
    if isinstance(report, CoachResponse):
        return text_response(report.model_dump_json())
    import json
    return text_response(json.dumps(report, ensure_ascii=False))


class MockLlmAdapter(ILlmAdapter):
    """Plays back `steps` in order. A step is a response, an exception to raise, or a callable
    receiving the LlmRequest (use it to assert on what the agent sent, e.g. images)."""

    def __init__(self, steps: list[Step] | None = None, *, supports_vision: bool = True) -> None:
        self.steps: list[Step] = list(steps or [])
        self.supports_vision = supports_vision
        self.requests: list[LlmRequest] = []

    def push(self, *steps: Step) -> "MockLlmAdapter":
        self.steps.extend(steps)
        return self

    @property
    def remaining(self) -> int:
        return len(self.steps)

    async def chat(self, request: LlmRequest) -> LlmResponse:
        if request.has_images and not self.supports_vision:
            raise LlmCapabilityError("mock model configured without vision")
        # Snapshot so later mutation of the agent's message list cannot rewrite history.
        self.requests.append(request.model_copy(deep=True))
        if not self.steps:
            raise MockLlmExhausted(f"mock LLM has no scripted response for call #{len(self.requests)}")
        step = self.steps.pop(0)
        if callable(step) and not isinstance(step, (LlmResponse, Exception)):
            step = step(request)
        if isinstance(step, Exception):
            raise step
        return step
