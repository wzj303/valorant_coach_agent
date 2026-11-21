"""AgentLoop: image + text in, validated CoachResponse out.

One agent, no framework. The loop sends the screenshots to a vision model as real image
parts, lets it pick a skill, execute registered tools over several turns, then checks the
final JSON against the shared CoachResponse contract (repairing it when needed).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from schemas import AnalysisContext, CoachResponse

from . import prompts
from .exceptions import AgentRequestError, LlmCapabilityError
from .llm_adapter import ILlmAdapter
from .llm_types import ImagePart, LlmRequest, LlmResponse, Message, ToolCall, ToolSpec, Usage
from .skill_loader import Skill, SkillLoader
from .tool_registry import ToolExecution, ToolRegistry, failed_result
from .utils import compact_json, extract_json_object, format_validation_error, strip_titles

log = logging.getLogger("agent")

LOAD_SKILL = "load_skill"
MAX_IMAGES = 10

ImageInput = Union[str, Path, bytes, ImagePart]


class AgentRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str = ""
    images: list[ImageInput] = Field(default_factory=list)
    task_type: str | None = None  # buy_review | position_review | match_review; None = let the agent choose
    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    context: AnalysisContext | None = None  # structured data already known (e.g. earlier rounds)
    user_observations: list[str] = Field(default_factory=list)


class AgentTrace(BaseModel):
    task_id: str
    skill: str | None = None
    iterations: int = 0
    tool_calls: int = 0
    repairs: int = 0
    tool_executions: list[ToolExecution] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    notes: list[str] = Field(default_factory=list)
    final_text: str | None = None


class AgentRunResult(BaseModel):
    response: CoachResponse
    trace: AgentTrace


class AgentLoop:
    def __init__(self, llm: ILlmAdapter, registry: ToolRegistry, skills: SkillLoader, *,
                 max_iterations: int = 12, max_tool_calls: int = 24, max_repairs: int = 2,
                 max_image_bytes: int | None = None) -> None:
        if max_iterations < 1 or max_tool_calls < 0 or max_repairs < 0:
            raise ValueError("max_iterations must be >= 1; max_tool_calls and max_repairs >= 0")
        self.llm, self.registry, self.skills = llm, registry, skills
        self.max_iterations, self.max_tool_calls, self.max_repairs = max_iterations, max_tool_calls, max_repairs
        self.max_image_bytes = max_image_bytes
        self._schema = strip_titles(CoachResponse.model_json_schema())

    # ------------------------------------------------------------------- public
    async def run(self, request: AgentRequest) -> CoachResponse:
        return (await self.run_with_trace(request)).response

    async def run_with_trace(self, request: AgentRequest) -> AgentRunResult:
        images = self._prepare_images(request)
        if not request.text.strip() and not images and request.context is None:
            raise AgentRequestError("request has no text, images or context")
        if images and not self.llm.supports_vision:
            raise LlmCapabilityError("this LLM adapter does not support image input")

        trace = AgentTrace(task_id=request.task_id)
        skill = self._initial_skill(request)
        trace.skill = skill.name if skill else None
        messages: list[Message] = [Message.system(self._system_prompt(skill)),
                                   Message.user(self._user_text(request, images), images)]

        final_text: str | None = None
        for _ in range(self.max_iterations):
            trace.iterations += 1
            budget_left = self.max_tool_calls - trace.tool_calls
            tools = self._tool_specs(skill) if budget_left > 0 else []
            reply = await self._chat(messages, tools, trace)
            messages.append(Message.assistant(reply.text, reply.tool_calls))
            if not reply.tool_calls:
                final_text = reply.text
                break
            skill = await self._handle_calls(reply.tool_calls, skill, messages, trace)
            messages[0] = Message.system(self._system_prompt(skill))
            trace.skill = skill.name if skill else None
        else:
            trace.notes.append(f"stopped after max_iterations={self.max_iterations} without a final answer")

        if final_text is None:
            messages.append(Message.user(prompts.FORCE_FINAL_PROMPT))
            reply = await self._chat(messages, [], trace)
            messages.append(Message.assistant(reply.text))
            final_text = reply.text

        response = await self._finalize(final_text, request, messages, trace)
        return AgentRunResult(response=response, trace=trace)

    async def report_from_evidence(self, request: AgentRequest, evidence_text: str,
                                   trace: AgentTrace | None = None) -> AgentRunResult:
        """Generate a report from host-executed tools without reopening tool selection."""
        trace = trace or AgentTrace(task_id=request.task_id)
        skill = self._initial_skill(request)
        trace.skill = skill.name if skill else None
        messages = [Message.system(self._system_prompt(skill) +
                    "\n宿主已完成数据校验和工具计算。只使用下方提供的结果写报告，不再调用工具。"
                    "历史回合仅用于比较，不得用于补齐当前截图未知字段。"),
                    Message.user(self._user_text(request, []) + "\n\n" + evidence_text)]
        trace.iterations += 1
        reply = await self._chat(messages, [], trace)
        messages.append(Message.assistant(reply.text))
        response = await self._finalize(reply.text, request, messages, trace)
        return AgentRunResult(response=response, trace=trace)

    # ------------------------------------------------------------------ request
    def _prepare_images(self, request: AgentRequest) -> list[ImagePart]:
        if len(request.images) > MAX_IMAGES:
            raise AgentRequestError(f"at most {MAX_IMAGES} images per request, got {len(request.images)}")
        limit = {"max_bytes": self.max_image_bytes} if self.max_image_bytes else {}
        out: list[ImagePart] = []
        for i, item in enumerate(request.images, 1):
            if isinstance(item, ImagePart):
                out.append(item)
            elif isinstance(item, (bytes, bytearray)):
                out.append(ImagePart.from_bytes(bytes(item), label=f"image-{i}", **limit))
            else:
                out.append(ImagePart.from_file(item, **limit))
        return out

    @staticmethod
    def _user_text(request: AgentRequest, images: list[ImagePart]) -> str:
        lines = [f"task_id: {request.task_id}"]
        if request.task_type:
            lines.append(f"task_type: {request.task_type}")
        lines.append("")
        lines.append(request.text.strip() or "(用户没有附加文字，请根据截图和任务类型分析。)")
        if images:
            lines += ["", "附带截图（按顺序，可用这些名字作为 source_images）："]
            lines += [f"  [{i}] {img.label or f'image-{i}'}" for i, img in enumerate(images, 1)]
        if request.user_observations:
            lines += ["", "用户补充（视为用户确认的信息）："] + [f"- {o}" for o in request.user_observations]
        if request.context is not None:
            lines += ["", "已有的结构化数据（AnalysisContext，JSON）：",
                      request.context.model_dump_json(exclude_none=True)]
        return "\n".join(lines)

    # -------------------------------------------------------------------- skill
    def _initial_skill(self, request: AgentRequest) -> Skill | None:
        if request.task_type:
            return self.skills.select(request.task_type)  # raises SkillNotFoundError if unknown
        return self.skills.select(None, request.text)  # None when unclear: the model picks

    def _system_prompt(self, skill: Skill | None) -> str:
        missing: list[str] = []
        if skill is not None and skill.allowed_tools:
            missing = [t for t in skill.allowed_tools if t not in self.registry]
        return prompts.build_system_prompt(
            skill_text=self.skills.render(skill) if skill else None,
            catalog=self.skills.catalog(), unavailable_tools=missing)

    def _tool_specs(self, skill: Skill | None) -> list[ToolSpec]:
        if skill is None:  # nothing but the skill chooser until a skill is active
            return [ToolSpec(name=LOAD_SKILL, description="加载一个 Skill，获得该任务的工作流程。",
                             parameters={"type": "object", "required": ["name"], "additionalProperties": False,
                                         "properties": {"name": {"type": "string",
                                                                 "enum": self.skills.names()}}})]
        return self.registry.specs(skill.tool_names)

    # --------------------------------------------------------------- LLM + tools
    async def _chat(self, messages: list[Message], tools: list[ToolSpec], trace: AgentTrace) -> LlmResponse:
        reply = await self.llm.chat(LlmRequest(messages=list(messages), tools=tools,
                                               response_schema=self._schema))
        trace.usage.add(reply.usage)
        return reply

    async def _handle_calls(self, calls: list[ToolCall], skill: Skill | None, messages: list[Message],
                            trace: AgentTrace) -> Skill | None:
        for call in calls:
            if call.name == LOAD_SKILL and skill is None and not call.parse_error:
                name = call.arguments.get("name")
                try:
                    skill = self.skills.get(str(name))
                    messages.append(Message.tool_result(call, f"skill {skill.name!r} loaded; follow its workflow."))
                except Exception as exc:
                    messages.append(Message.tool_result(call, str(exc), is_error=True))
                continue
            if trace.tool_calls >= self.max_tool_calls:
                result = failed_result(call.name, f"tool-call budget of {self.max_tool_calls} is used up; "
                                                  "answer with the information you already have")
                messages.append(Message.tool_result(call, compact_json(result), is_error=True))
                continue
            trace.tool_calls += 1
            execution = await self.registry.execute(call, skill.tool_names if skill else frozenset())
            trace.tool_executions.append(execution)
            log.info("tool %s -> %s (%d ms)", call.name, execution.status, execution.duration_ms)
            messages.append(Message.tool_result(call, execution.content, is_error=not execution.ok))
        return skill

    # ------------------------------------------------------------------- output
    async def _finalize(self, text: str, request: AgentRequest, messages: list[Message],
                        trace: AgentTrace) -> CoachResponse:
        problems: list[str] = []
        for attempt in range(self.max_repairs + 1):
            trace.final_text = text
            parsed = self._parse_report(text, request, trace, problems)
            if parsed is not None:
                return self._apply_guardrails(parsed, trace)
            if attempt == self.max_repairs:
                break
            trace.repairs += 1
            messages.append(Message.user(prompts.REPAIR_PROMPT.format(problems="\n".join(f"- {p}" for p in problems))))
            reply = await self._chat(messages, [], trace)
            messages.append(Message.assistant(reply.text))
            text = reply.text
        trace.notes.append("final answer never became a valid CoachResponse: " + "; ".join(problems[:3]))
        return CoachResponse(
            task_id=request.task_id, task_status="failed",
            missing_information=["模型没有给出符合格式的分析报告: " + "; ".join(problems[:3])],
            coach_summary="这次分析没有成功完成，没有可靠的结论可以给出。请重试，或补充更清晰的截图。")

    def _parse_report(self, text: str, request: AgentRequest, trace: AgentTrace,
                      problems: list[str]) -> CoachResponse | None:
        problems.clear()
        data = extract_json_object(text or "")
        if data is None:
            problems.append("回复里没有找到 JSON 对象" if (text or "").strip() else "回复是空的")
            return None
        if data.get("task_id") != request.task_id:
            if data.get("task_id") is not None:
                trace.notes.append(f"model task_id {data.get('task_id')!r} replaced with {request.task_id!r}")
            data["task_id"] = request.task_id
        recs = data.get("recommendations")
        if isinstance(recs, list) and len(recs) > 3:
            recs = sorted(recs, key=lambda r: r.get("priority", 99) if isinstance(r, dict) and isinstance(r.get("priority"), int) else 99)
            data["recommendations"] = recs[:3]
            trace.notes.append("recommendations trimmed to the 3 highest-priority items")
        try:
            return CoachResponse.model_validate_json(compact_json(data))
        except ValidationError as exc:
            problems.extend(format_validation_error(exc))
        except ValueError as exc:
            problems.append(str(exc))
        return None

    @staticmethod
    def _apply_guardrails(report: CoachResponse, trace: AgentTrace) -> CoachResponse:
        """Deterministic rules the model cannot talk its way around."""
        # Latest call per tool: a failure that a later call of the same tool fixed counts as recovered.
        latest: dict[str, ToolExecution] = {}
        for ex in trace.tool_executions:
            latest[ex.tool_name] = ex
        updates: dict[str, Any] = {}
        if report.task_status == "success" and any(ex.status != "success" for ex in latest.values()):
            updates["task_status"] = "partial"
            trace.notes.append("task_status lowered to partial: a tool failed or returned partial data")
        missing = list(report.missing_information)
        for ex in latest.values():
            if ex.status == "failed":
                reason = (ex.result.get("errors") or ["unknown error"])[0]
                line = f"工具 {ex.tool_name} 未成功: {reason}"
                if not any(ex.tool_name in m for m in missing):
                    missing.append(line)
        if missing != report.missing_information:
            updates["missing_information"] = missing
        return report.model_copy(update=updates) if updates else report
