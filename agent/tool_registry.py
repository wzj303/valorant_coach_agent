"""ToolRegistry: the only way the model can make Python run.

The model names a registered tool and supplies JSON arguments. The registry validates them
against the tool's Pydantic model (the shared contracts in `schemas`), calls the registered
function, checks the output against the tool's result contract, and always hands back a
ToolResult-shaped dict, including for unknown tools, bad arguments, exceptions and timeouts.
Nothing the model writes is ever evaluated as code.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from schemas import ToolResult

from .exceptions import ToolRegistrationError
from .llm_types import ToolCall, ToolSpec
from .utils import compact_json, decode_json_string, format_validation_error, strip_titles

_VALID_STATUS = {"success", "partial", "failed"}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Callable[..., Any]
    input_model: type[BaseModel] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema shown to the model
    result_model: Any | None = None  # e.g. ToolResult[BuyStateAnalysis]; None = shape check only
    raw_arguments: bool = False  # True: handler gets the raw argument dict (it validates itself)
    timeout_s: float = 30.0


class ToolExecution(BaseModel):
    """What happened for one tool call. `content` is what the model reads back."""

    call_id: str
    tool_name: str
    status: str  # success | partial | failed
    content: str  # compact JSON, always a ToolResult-shaped object
    result: dict[str, Any]
    duration_ms: int = 0
    failure_kind: str | None = None  # unknown_tool | bad_arguments | tool_error | timeout | bad_output

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def failed_result(tool_name: str, *errors: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return ToolResult(tool_name=tool_name or "unknown", status="failed", data=None,
                      errors=[e for e in errors if e], warnings=warnings or []).model_dump(mode="json")


class ToolRegistry:
    def __init__(self, *, validate_results: bool = True) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.validate_results = validate_results

    # ------------------------------------------------------------ registration
    def register(self, name: str, handler: Callable[..., Any], *, description: str,
                 input_model: type[BaseModel] | None = None, parameters: dict[str, Any] | None = None,
                 result_model: Any | None = None, raw_arguments: bool = False,
                 timeout_s: float = 30.0) -> ToolDefinition:
        if not name or not name.replace("_", "").isalnum() or len(name) > 64:
            raise ToolRegistrationError(f"invalid tool name: {name!r}")
        if name in self._tools:
            raise ToolRegistrationError(f"tool already registered: {name}")
        if not callable(handler):
            raise ToolRegistrationError(f"handler for {name} is not callable")
        if input_model is None and parameters is None:
            raise ToolRegistrationError(f"{name}: give input_model or parameters")
        schema = parameters if parameters is not None else input_model.model_json_schema()  # type: ignore[union-attr]
        definition = ToolDefinition(name=name, description=description, handler=handler,
                                    input_model=input_model, parameters=strip_titles(schema),
                                    result_model=result_model, raw_arguments=raw_arguments,
                                    timeout_s=timeout_s)
        self._tools[name] = definition
        return definition

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def specs(self, allowed: set[str] | frozenset[str] | None = None) -> list[ToolSpec]:
        """Tool definitions for the model, optionally limited to `allowed` names."""
        return [ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
                for t in self._tools.values() if allowed is None or t.name in allowed]

    # --------------------------------------------------------------- execution
    async def execute(self, call: ToolCall, allowed: set[str] | frozenset[str] | None = None) -> ToolExecution:
        """Run one tool call. `allowed` restricts which registered tools this call may reach."""
        started = time.perf_counter()
        result, status, kind = await self._run(call, allowed)
        return ToolExecution(call_id=call.id, tool_name=call.name, status=status,
                             content=compact_json(result), result=result, failure_kind=kind,
                             duration_ms=int((time.perf_counter() - started) * 1000))

    async def _run(self, call: ToolCall,
                   allowed: set[str] | frozenset[str] | None) -> tuple[dict[str, Any], str, str | None]:
        name = call.name
        if call.parse_error:
            return failed_result(name, f"tool arguments rejected: {call.parse_error}"), "failed", "bad_arguments"
        tool = self._tools.get(name)
        if tool is None or (allowed is not None and name not in allowed):
            available = [n for n in self._tools if allowed is None or n in allowed]
            return (failed_result(name, f"unknown tool {name!r}",
                                  warnings=["available tools: " + ", ".join(available)]),
                    "failed", "unknown_tool")
        if not isinstance(call.arguments, dict):
            return failed_result(name, "tool arguments must be a JSON object"), "failed", "bad_arguments"

        payload: Any = call.arguments
        if tool.input_model is not None and not tool.raw_arguments:
            parsed = self._validate(tool.input_model, call.arguments)
            if isinstance(parsed, list):
                return (failed_result(name, "invalid arguments; fix these and call the tool again:", *parsed),
                        "failed", "bad_arguments")
            payload = parsed

        try:
            output = await self._invoke(tool, payload)
        except asyncio.TimeoutError:
            return failed_result(name, f"tool timed out after {tool.timeout_s:g}s"), "failed", "timeout"
        except Exception as exc:  # tool code is untrusted-for-stability: never let it kill the run
            return (failed_result(name, f"tool raised {type(exc).__name__}: {str(exc)[:300]}"),
                    "failed", "tool_error")
        return self._normalize(tool, output)

    @staticmethod
    def _validate(model: type[BaseModel], arguments: dict[str, Any]) -> BaseModel | list[str]:
        def attempt(args: dict[str, Any]) -> BaseModel:
            # JSON mode: the contracts are strict, and arguments really are JSON.
            return model.model_validate_json(json.dumps(args, ensure_ascii=False))

        try:
            return attempt(arguments)
        except ValidationError as first:
            # Models sometimes send a nested object as a JSON *string*. Decode and retry once.
            decoded = {k: decode_json_string(v) for k, v in arguments.items()}
            if decoded != arguments:
                try:
                    return attempt(decoded)
                except ValidationError:
                    pass
            return format_validation_error(first)

    async def _invoke(self, tool: ToolDefinition, payload: Any) -> Any:
        args: tuple[Any, ...] = ()
        kwargs: dict[str, Any] = {}
        if tool.raw_arguments or not isinstance(payload, BaseModel):
            if isinstance(payload, dict):
                kwargs = dict(payload)
            else:
                args = (payload,)
        else:
            args, kwargs = _bind_model(tool, payload)
        if inspect.iscoroutinefunction(tool.handler):
            return await asyncio.wait_for(tool.handler(*args, **kwargs), tool.timeout_s)
        return await asyncio.wait_for(asyncio.to_thread(tool.handler, *args, **kwargs), tool.timeout_s)

    def _normalize(self, tool: ToolDefinition, output: Any) -> tuple[dict[str, Any], str, str | None]:
        name = tool.name
        if isinstance(output, BaseModel):
            output = output.model_dump(mode="json")
        if not isinstance(output, dict):
            return (failed_result(name, f"tool returned {type(output).__name__}, expected a ToolResult"),
                    "failed", "bad_output")
        status = output.get("status")
        if status not in _VALID_STATUS or "tool_name" not in output:
            return (failed_result(name, "tool output is not a ToolResult (needs tool_name and status)"),
                    "failed", "bad_output")
        if self.validate_results and tool.result_model is not None:
            try:
                tool.result_model.model_validate_json(json.dumps(output, ensure_ascii=False))
            except ValidationError as exc:
                problems = format_validation_error(exc, limit=6)
                return (failed_result(name, "tool output violates its result contract:", *problems),
                        "failed", "bad_output")
        return output, status, None


def _bind_model(tool: ToolDefinition, payload: BaseModel) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Call a handler either as f(request_model) or as f(round_snapshot=..., strategies=...).

    Chosen from the handler's signature: if it has a parameter for every required field of the
    input contract, the fields are passed by keyword (as typed models, not dicts). Otherwise the
    whole validated model is passed as the single positional argument.
    """
    try:
        params = inspect.signature(tool.handler).parameters.values()
    except (TypeError, ValueError):
        return (payload,), {}
    named = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    accepts_any = any(p.kind is p.VAR_KEYWORD for p in params)
    model_fields = type(payload).model_fields
    required = {n for n, f in model_fields.items() if f.is_required()}
    if required and required <= named:
        fields = {n: getattr(payload, n) for n in model_fields if accepts_any or n in named}
        return (), fields
    return (payload,), {}
