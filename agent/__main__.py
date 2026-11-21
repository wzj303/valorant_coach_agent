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

from .agent_loop import AgentLoop, AgentRequest
from .demo import DemoLlm
from .diagnostics import check_capabilities
from .exceptions import AgentError, AgentRequestError
from .llm_adapter import LlmConfig, RealLlmAdapter
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
    p.add_argument("--snapshot", type=Path, help="use a RoundSnapshot or PositionSnapshot JSON file in the offline demo")
    p.add_argument("--check-llm", action="store_true", help="probe the configured model and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p




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

    skills = SkillLoader()
    task = args.task
    snapshots = None
    if args.snapshot:
        if not args.demo:
            raise AgentRequestError("--snapshot requires --demo")
        try:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise AgentRequestError(f"cannot read snapshot: {exc}") from exc
        if not isinstance(snapshot, dict):
            raise AgentRequestError("--snapshot expects a JSON object")
        if task is None:
            task = "position_review" if "snapshot_id" in snapshot else "buy_review"
        snapshots = [snapshot]
    if args.demo and not task:
        selected = skills.select(None, args.text)
        task = next(iter(selected.task_types)) if selected else "buy_review"
    text = args.text or ("运行离线样例" if args.demo else "")
    request = AgentRequest(text=text, images=list(args.image), task_type=task,
                           user_observations=list(args.observation))
    registry = build_registry(expected_map_revision=args.map_revision, strict=True)
    skills.validate_tools(registry.names())
    llm = DemoLlm(request.task_id, task, snapshots) if args.demo else RealLlmAdapter(LlmConfig.from_env())
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
