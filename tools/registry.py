"""Tool wiring for the agent. Owned by GPT-1.

`build_registry()` registers the coach tools using the shared contracts in `schemas`:
  * validate_round_data lives in tools/validate_round_data.py (GPT-2).
  * tools/domain/contracts.py adapts the five tactical tools to the shared contracts.
  * get_coaching_advice retrieves passages from the bundled PC match review guide.

Convention for a domain tool module (see README "Integration with GPT-3"):

    tools/domain/analyze_buy_state.py  ->  def analyze_buy_state(request: BuyStateInput) -> ToolResult[...]

The function receives the already-validated input model and returns a ToolResult (model or
dict). It may be sync or async. A tool whose module is not importable yet is skipped, and the
skills tell the model to work around it instead of crashing.
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Callable

from schemas import TOOL_CONTRACTS
from schemas.validation import ValidateRoundInput

from agent.tool_registry import ToolRegistry

log = logging.getLogger("agent.tools")

DOMAIN_TOOLS = ("analyze_buy_state", "simulate_buy_options", "get_game_rules",
                "lookup_tactics", "analyze_positions", "get_coaching_advice")

DESCRIPTIONS = {
    "get_coaching_advice": (
        "从中文端游复盘知识库按关键词、中英文地图名或条目编号获取专业建议。"
        "返回原文、适用条件、例外、版本和来源位置。可按 rules、mechanics、map_notes 等章节筛选。"
        "示例仅用于对照，引用建议时结合本场可观察证据。"),
    "validate_round_data": (
        "检查并标准化你提取的 RoundSnapshot / PositionSnapshot。其他工具必须使用它返回的 "
        "data.normalized_snapshot。不会猜测缺失值，只报告缺失、疑点和错误。"),
    "analyze_buy_state": (
        "分析一个回合的双方经济、武器/护甲分布、技能购买情况和购买类型。所有经济数字都应取自此工具。"
        "缺失的金币不会被当成 0，敌方推测值会标注 estimated。"),
    "simulate_buy_options": (
        "按全起/半起/eco 模拟可行的购买方案，检查预算并给出下一回合经济影响。"
        "只列出可行方案，不宣称某个方案绝对最优。缺少购买前金币时返回无法计算的原因。"),
    "get_game_rules": (
        "查询指定游戏版本的价格、经济和连败奖励规则，附来源和验证时间。版本不支持或未验证时会明确说明。"),
    "lookup_tactics": (
        "查询 Sunset 地图的区域、默认站位、攻防原则、英雄职责和技能协同等战术知识，返回带来源的条目。"),
    "analyze_positions": (
        "分析一个或多个 PositionSnapshot 的区域分布、玩家间距、孤立玩家和潜在支援关系。"
        "区域人数不代表控制权；单帧不能推断移动轨迹。"),
}


def _maybe_decode(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def make_validate_handler(expected_map_revision: str | None = None) -> Callable[..., Any]:
    """Wrap validate_round_data. The map revision is trusted host context, never a model argument."""
    from tools.validate_round_data import validate_round_data

    allowed = set(ValidateRoundInput.model_fields)

    def handler(**arguments: Any) -> Any:
        extra = sorted(set(arguments) - allowed)
        if extra:
            return _failed("validate_round_data",
                           f"unexpected argument(s): {', '.join(extra)}; allowed: {', '.join(sorted(allowed))}")
        decoded = {k: _maybe_decode(v) for k, v in arguments.items()}
        return validate_round_data(**decoded, expected_map_revision=expected_map_revision)

    return handler


def _failed(tool_name: str, message: str) -> dict[str, Any]:
    return {"tool_name": tool_name, "status": "failed", "data": None, "warnings": [],
            "errors": [message], "evidence": []}


def load_domain_handler(name: str) -> Callable[..., Any] | None:
    """Import tools.domain.<name>.<name>. Returns None while GPT-3's module is absent."""
    module_name = f"tools.domain.{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            try:
                adapters = importlib.import_module("tools.domain.contracts")
            except ModuleNotFoundError as missing:
                if missing.name == "tools.domain.contracts":
                    return None
                raise
            return adapters.HANDLERS.get(name)
        if exc.name in ("tools.domain", "tools"):
            return None
        raise  # the module exists but one of its own imports is broken: do not hide that
    handler = getattr(module, name, None)
    if not callable(handler):
        log.warning("%s exists but defines no callable %r; tool skipped", module_name, name)
        return None
    return handler


def build_registry(*, expected_map_revision: str | None = None, strict: bool = False,
                   handlers: dict[str, Callable[..., Any]] | None = None) -> ToolRegistry:
    """Create the registry. `handlers` overrides or supplies tools (used by tests and by GPT-3
    integration). With strict=True a missing domain tool raises instead of being skipped."""
    registry = ToolRegistry()
    overrides = dict(handlers or {})

    in_model, out_model = TOOL_CONTRACTS["validate_round_data"]
    registry.register("validate_round_data",
                      overrides.pop("validate_round_data", None) or make_validate_handler(expected_map_revision),
                      description=DESCRIPTIONS["validate_round_data"], input_model=in_model,
                      result_model=out_model, raw_arguments=True)

    missing: list[str] = []
    for name in DOMAIN_TOOLS:
        handler = overrides.pop(name, None) or load_domain_handler(name)
        if handler is None:
            missing.append(name)
            continue
        in_model, out_model = TOOL_CONTRACTS[name]
        registry.register(name, handler, description=DESCRIPTIONS[name], input_model=in_model,
                          result_model=out_model)
    if overrides:
        raise ValueError(f"unknown tool name(s) in handlers: {', '.join(overrides)}")
    if missing:
        message = "domain tools not available yet: " + ", ".join(missing)
        if strict:
            raise RuntimeError(message)
        log.warning(message)
    return registry
