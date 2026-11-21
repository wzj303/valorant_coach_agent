"""Exercise the real registry and tactical services through the public entry points."""
import json
from pathlib import Path

import pytest

from agent.__main__ import main
from agent.llm_types import ToolCall
from schemas import CoachResponse, TOOL_CONTRACTS
from tools.registry import build_registry

FIXTURES = Path(__file__).resolve().parents[1] / "schemas" / "fixtures"


@pytest.mark.parametrize(("task", "tools", "report_field", "expected"), [
    ("buy_review", ["validate_round_data", "analyze_buy_state", "get_game_rules", "simulate_buy_options"],
     "buy_review", "4199"),
    ("position_review", ["validate_round_data", "analyze_positions", "lookup_tactics"],
     "position_review", "A Site"),
    ("match_review", ["validate_round_data", "analyze_buy_state"], "buy_review", "3200"),
])
def test_cli_reports_from_live_tools(task, tools, report_field, expected, capsys):
    assert main(["--demo", "--task", task, "--json", "--trace"]) == 0
    output = capsys.readouterr()
    report = CoachResponse.model_validate_json(output.out)
    assert expected in getattr(report, report_field)
    assert report.task_status == "partial" and report.missing_information
    assert all(f"{tool}: partial" in output.err for tool in tools)
    assert ": failed" not in output.err


def test_default_and_external_input(capsys):
    assert main(["--demo"]) == 0
    assert "4199" in capsys.readouterr().out
    assert main(["--demo", "--snapshot", str(FIXTURES / "complete_round.json"), "--json"]) == 0
    report = CoachResponse.model_validate_json(capsys.readouterr().out)
    assert "22500" in report.buy_review and "4199" not in report.buy_review
    assert main(["--demo", "--snapshot", str(FIXTURES / "invalid_round.json"), "--json"]) == 2
    assert CoachResponse.model_validate_json(capsys.readouterr().out).task_status == "failed"


async def run_tool(name, **arguments):
    registry = build_registry(strict=True)
    assert set(registry.names()) == set(TOOL_CONTRACTS)
    ex = await registry.execute(ToolCall(id="test", name=name, arguments=arguments))
    assert ex.failure_kind is None, ex.result
    return TOOL_CONTRACTS[name][1].model_validate(ex.result)


async def test_estimated_money_and_unverified_prices():
    r = json.loads((FIXTURES / "partial_round.json").read_text(encoding="utf-8"))
    out = await run_tool("analyze_buy_state", round_snapshot=r)
    assert out.data.enemy_economy.estimated and out.data.enemy_economy.known_credits == 3000
    assert out.data.ally_economy.known_credits is None
    rules = await run_tool("get_game_rules", game_patch="unsupported", rule_categories=["weapons", "armor"])
    assert rules.status == "partial" and rules.data.weapon_prices is None
    sim = await run_tool("simulate_buy_options", round_snapshot=r, strategies=["full_buy", "eco"])
    assert sim.data.plans[0].players[0].spend is None
    assert sim.data.plans[1].players[0].spend == 0
    assert sim.data.plans[1].remaining_credits is None


async def test_position_counts_distances_and_revision():
    p = json.loads((FIXTURES / "position_snapshot.json").read_text(encoding="utf-8"))
    p["players"][1].update(x=0.5, y=0.8)
    out = await run_tool("analyze_positions", position_snapshots=[p], map_id=p["map_id"],
                         map_revision=p["map_revision"])
    assert out.data.region_distribution[0].counts == {"A Site": {"ally": 1}, "Mid": {"ally": 1}}
    assert out.data.player_distances[0].distance == pytest.approx(0.5)
    assert out.data.player_distances[0].unit == "normalized"
    tactics = await run_tool("lookup_tactics", map_id=p["map_id"], map_revision=p["map_revision"], query="Sunset")
    assert tactics.status == "partial" and tactics.data.entries == []
