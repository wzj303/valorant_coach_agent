"""Helpers for the agent tests: a real PNG, schema-valid fake tools, report builders."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

from schemas import TOOL_CONTRACTS, CoachResponse
from schemas.tool_contracts import (BuyPlan, BuyPlanComparison, BuyStateAnalysis, GameRules,
                                    PositionAnalysis, TacticEntry, TacticQueryResult, TeamEconomy)
from schemas.tool_result import ToolResult

ROOT = Path(__file__).resolve().parents[2]


def make_png(width: int = 4, height: int = 4) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jett_snapshot() -> dict[str, Any]:
    """GPT-2's confirmed single-player example, expressed as raw tool arguments."""
    return {"match_id": "m1", "round_id": "m1-r1", "round_index": 1, "map_id": "sunset",
            "map_revision": None,
            "ally_players": [{"slot": 1, "team": "ally", "agent": "Jett", "weapon": "Vandal",
                              "credits_unclassified": 4199, "armor": "light",
                              "evidence": [{"field_name": "agent", "status": "observed", "source": "screenshot"},
                                           {"field_name": "weapon", "status": "observed", "source": "screenshot"}]}]}


def _result(name: str, status: str, data: Any, warning: str | None = None) -> ToolResult:
    """Build the result with the contract's parametrised type, as GPT-2's integration example does."""
    result_type = TOOL_CONTRACTS[name][1]
    return result_type(tool_name=name, status=status, data=data, warnings=[warning] if warning else [])


def _partial(name: str, data: Any, warning: str = "only one player was read") -> ToolResult:
    return _result(name, "partial", data, warning)


def fake_handlers() -> dict[str, Any]:
    """Domain tools that return schema-valid partial results. Stand-ins for GPT-3's tools."""
    eco = TeamEconomy(known_credits=None, known_players=0, time_basis="before_buy", estimated=False, complete=False)

    def analyze_buy_state(request):
        data = BuyStateAnalysis(
            ally_economy=eco, enemy_economy=eco, weapon_distribution={"ally": {"Vandal": 1}, "enemy": {}},
            armor_distribution={"ally": {"light": 1}, "enemy": {}}, ability_completeness={"ally": None, "enemy": None},
            buy_type={"ally": None, "enemy": None}, team_buy_coordination=None,
            equipment_value={"ally": None, "enemy": None}, warnings=[], evidence=[])
        return _partial("analyze_buy_state", data)

    def simulate_buy_options(request):
        plans = [BuyPlan(plan_id=f"p-{s}", strategy=s, feasible=None, players=[], total_spend=None,
                         remaining_credits=None, next_round_projection=None, warnings=[],
                         assumptions=["missing credits_before_buy"]) for s in request.strategies]
        return _partial("simulate_buy_options", BuyPlanComparison(plans=plans), "cannot compute without balances")

    def get_game_rules(request):
        data = GameRules(game_patch=request.game_patch, weapon_prices=None, armor_prices=None,
                         ability_prices=None, round_economy_rules=None, loss_bonus_rules=None,
                         kill_rewards=None, source=None, verified_at=None, completeness=0.0,
                         warnings=["patch not verified"])
        return _partial("get_game_rules", data, "rules for this patch are not verified")

    def lookup_tactics(request):
        entry = TacticEntry(rule_id="sunset-001", title="Mid control", content="Contest mid early.",
                            applicable_conditions=["defense"], source="fixture", map_revision=request.map_revision)
        data = TacticQueryResult(entries=[entry], map_id="sunset", map_revision=request.map_revision,
                                 matched_count=1, truncated=False, warnings=[])
        return _result("lookup_tactics", "success", data)

    def analyze_positions(request):
        data = PositionAnalysis(region_distribution=[], player_distances=[], isolated_players={},
                                support_candidates=[], formation_summary=None,
                                changes_between_snapshots=[], warnings=[], evidence=[])
        return _partial("analyze_positions", data, "no coordinates")

    return {"analyze_buy_state": analyze_buy_state, "simulate_buy_options": simulate_buy_options,
            "get_game_rules": get_game_rules, "lookup_tactics": lookup_tactics,
            "analyze_positions": analyze_positions}


def report(task_id: str, **overrides: Any) -> CoachResponse:
    base: dict[str, Any] = dict(
        task_id=task_id, task_status="partial", overall_judgement="信息不足，只能给出初步判断。",
        buy_review="Jett 带 Vandal 和轻甲，团队信息缺失。",
        evidence=[{"field_name": "agent", "status": "observed", "source": "screenshot", "confidence": 0.9},
                  {"field_name": "analyze_buy_state.weapon_distribution", "status": "derived", "source": "tool"}],
        findings=[{"title": "队友信息缺失", "severity": "low", "explanation": "只识别出一名玩家。",
                   "evidence_refs": ["agent"], "confidence": 0.6}],
        recommendations=[{"priority": 1, "action": "补充队友截图", "reason": "团队协调无法判断",
                          "expected_benefit": "能评价团队购买"}],
        missing_information=["队友装备"], coach_summary="先补全队友信息，再评价这次购买。")
    base.update(overrides)
    return CoachResponse.model_validate(base)
