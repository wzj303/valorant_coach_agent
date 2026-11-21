"""CLI and report rendering (offline, uses the scripted demo model)."""
from __future__ import annotations

import json

import pytest

from agent.__main__ import build_parser, main
from agent.diagnostics import check_capabilities, solid_png
from agent.llm_types import ImagePart
from agent.mock_llm import MockLlmAdapter, text_response, tool_call_response
from agent.report import render_markdown
from tests.agent.helpers import report


def test_demo_runs_offline_and_prints_a_report(capsys):
    assert main(["--demo", "--task", "buy_review", "--text", "x"]) == 0
    out = capsys.readouterr().out
    assert "# 教练报告" in out and "部分完成" in out and "金币时点不明" in out


def test_demo_json_output_is_a_valid_coach_response(capsys):
    from schemas import CoachResponse
    assert main(["--demo", "--json", "--task", "buy_review", "--text", "x"]) == 0
    CoachResponse.model_validate_json(capsys.readouterr().out)


def test_no_input_is_a_usage_error(capsys):
    assert main([]) == 64 and "nothing to analyse" in capsys.readouterr().err


def test_missing_credentials_is_a_clean_error_not_a_traceback(capsys, monkeypatch):
    for k in ("VALORANT_LLM_API_KEY", "VALORANT_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(__import__("tempfile").mkdtemp())     # no .env here
    assert main(["--task", "buy_review", "--text", "x"]) == 1
    assert "VALORANT_LLM_API_KEY" in capsys.readouterr().err


def test_bad_image_path_is_a_clean_error(capsys):
    assert main(["--demo", "--task", "buy_review", "--image", "nope.png"]) == 1
    assert "cannot read image" in capsys.readouterr().err


def test_parser_defaults():
    a = build_parser().parse_args(["--text", "hi"])
    assert a.task is None and a.image == [] and not a.json


def test_markdown_contains_every_section_and_sorts_recommendations():
    r = report("t", recommendations=[
        {"priority": 2, "action": "B", "reason": "r", "expected_benefit": "b"},
        {"priority": 1, "action": "A", "reason": "r", "expected_benefit": "b"}], position_review="站位结论")
    md = render_markdown(r)
    for part in ("总体判断", "购买复盘", "站位复盘", "发现", "建议", "依据", "缺失信息", "总结"):
        assert part in md
    assert md.index("**A**") < md.index("**B**")


def test_markdown_for_minimal_report_has_no_empty_sections():
    from schemas import CoachResponse
    md = render_markdown(CoachResponse(task_id="t", task_status="failed", coach_summary="没有完成"))
    assert "## 发现" not in md and "## 建议" not in md and "没有完成" in md


async def test_capability_probe_passes_a_good_model():
    llm = MockLlmAdapter([
        text_response("pong"),
        lambda req: text_response("blue") if req.has_images else text_response("?"),
        tool_call_response(("record_color", {"color": "green"}))])
    probes = await check_capabilities(llm)
    assert [(p.name, p.passed) for p in probes] == [("text", True), ("vision", True), ("tool_calling", True)]


async def test_capability_probe_catches_a_gateway_that_drops_images():
    llm = MockLlmAdapter([text_response("pong"), text_response("I cannot see any image."),
                          text_response("done")])
    probes = {p.name: p for p in await check_capabilities(llm)}
    assert not probes["vision"].passed and "dropping images" in probes["vision"].detail
    assert not probes["tool_calling"].passed


async def test_capability_probe_reports_errors_instead_of_raising():
    probes = await check_capabilities(MockLlmAdapter([RuntimeError("boom")]))
    assert not probes[0].passed and "boom" in probes[0].detail


def test_probe_image_is_a_valid_png():
    assert ImagePart.from_bytes(solid_png((0, 0, 255))).media_type == "image/png"
