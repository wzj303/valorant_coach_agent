"""Command line entry point.

    python -m agent --task buy_review --image shot.png --text "这回合该怎么买？"
    python -m agent --check-llm          # probe text / vision / tool calling of the configured model
    python -m agent --demo               # offline run with a scripted mock model (no API key)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from schemas import CoachResponse

from .agent_loop import AgentLoop, AgentRequest
from .diagnostics import check_capabilities
from .exceptions import AgentError
from .llm_adapter import ILlmAdapter, LlmConfig, RealLlmAdapter
from .mock_llm import MockLlmAdapter, final_response, tool_call_response
from .report import render_markdown
from .skill_loader import SkillLoader

TASKS = ("buy_review", "position_review", "match_review")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional at runtime
        return
    load_dotenv(Path.cwd() / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent", description="VALORANT AI Coach agent")
    p.add_argument("--task", choices=TASKS, help="task type; omitted = the agent chooses a skill")
    p.add_argument("--text", default="", help="your question or notes")
    p.add_argument("--image", action="append", default=[], metavar="PATH", help="screenshot (repeatable)")
    p.add_argument("--observation", action="append", default=[], metavar="TEXT",
                   help="a fact you confirm, treated as user_confirmed (repeatable)")
    p.add_argument("--json", action="store_true", help="print the CoachResponse JSON instead of Markdown")
    p.add_argument("--trace", action="store_true", help="also print tool calls and token usage to stderr")
    p.add_argument("--map-revision", help="trusted map revision from your map data (used by validate_round_data)")
    p.add_argument("--max-iterations", type=int, default=12)
    p.add_argument("--demo", action="store_true", help="run offline with a scripted mock model")
    p.add_argument("--check-llm", action="store_true", help="probe the configured model and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def demo_llm(task_id: str) -> MockLlmAdapter:
    """Scripted stand-in model so the whole pipeline can be seen without an API key."""
    snapshot = {"match_id": "demo", "round_id": "demo-r1", "round_index": 1, "map_id": "sunset",
                "map_revision": None,
                "ally_players": [{"slot": 1, "team": "ally", "agent": "Jett", "weapon": "Vandal",
                                  "credits_unclassified": 4199, "armor": "light",
                                  "evidence": [{"field_name": "agent", "status": "user_confirmed", "source": "user_input"},
                                               {"field_name": "weapon", "status": "user_confirmed", "source": "user_input"}]}]}
    report = CoachResponse.model_validate({
        "task_id": task_id, "task_status": "partial",
        "overall_judgement": "只确认了 Jett 一个人的装备，无法评价整队购买。",
        "buy_review": "Jett 持 Vandal 并穿轻甲。4199 金币的时点未确认，不能判断这是购买前还是购买后。",
        "findings": [{"title": "金币时点不明", "severity": "medium",
                      "explanation": "4199 只记为 credits_unclassified，没有用于买枪预算。",
                      "evidence_refs": ["credits_unclassified"], "confidence": 0.9}],
        "evidence": [{"field_name": "credits_unclassified", "status": "user_confirmed", "source": "user_input"}],
        "recommendations": [{"priority": 1, "action": "确认 4199 金币是购买前还是购买后的余额",
                             "reason": "购买预算只能基于购买前金币计算", "expected_benefit": "可以进行购买方案模拟"}],
        "missing_information": ["队友和敌方的装备与金币", "游戏版本 game_patch"],
        "coach_summary": "先确认金币时点，再补全队友信息，之后才能判断这次购买是否合理。（演示数据）"})
    return MockLlmAdapter([tool_call_response(("validate_round_data", {"round_snapshot": snapshot})),
                           final_response(report)])


def make_llm(args: argparse.Namespace, task_id: str) -> ILlmAdapter:
    if args.demo:
        return demo_llm(task_id)
    return RealLlmAdapter(LlmConfig.from_env())


async def run_cli(args: argparse.Namespace) -> int:
    from tools.registry import build_registry

    if args.check_llm:
        llm = RealLlmAdapter(LlmConfig.from_env())
        try:
            probes = await check_capabilities(llm)
        finally:
            await llm.aclose()
        for pr in probes:
            print(f"[{'PASS' if pr.passed else 'FAIL'}] {pr.name}: {pr.detail}")
        return 0 if all(pr.passed for pr in probes) else 1

    request = AgentRequest(text=args.text, images=list(args.image), task_type=args.task,
                           user_observations=list(args.observation))
    registry = build_registry(expected_map_revision=args.map_revision)
    skills = SkillLoader()  # tools may be absent at this stage: skills degrade, see prompts.UNAVAILABLE_TOOLS_NOTE
    llm = make_llm(args, request.task_id)
    try:
        loop = AgentLoop(llm, registry, skills, max_iterations=args.max_iterations)
        result = await loop.run_with_trace(request)
    finally:
        await llm.aclose()
    if args.trace:
        t = result.trace
        print(f"skill={t.skill} iterations={t.iterations} tool_calls={t.tool_calls} repairs={t.repairs} "
              f"tokens_in={t.usage.input_tokens} tokens_out={t.usage.output_tokens}", file=sys.stderr)
        for ex in t.tool_executions:
            print(f"  {ex.tool_name}: {ex.status} ({ex.duration_ms} ms)", file=sys.stderr)
        for note in t.notes:
            print(f"  note: {note}", file=sys.stderr)
    print(result.response.model_dump_json(indent=2) if args.json else render_markdown(result.response))
    return 0 if result.response.task_status != "failed" else 2


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not args.demo and not args.check_llm and not args.text.strip() and not args.image:
        print("nothing to analyse: give --text and/or --image (or try --demo)", file=sys.stderr)
        return 64
    try:
        return asyncio.run(run_cli(args))
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
