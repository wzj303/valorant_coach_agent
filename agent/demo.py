"""Offline coach. Only the model is scripted; all tool messages come from the registry."""
import json

from schemas import CoachResponse

from .llm_adapter import ILlmAdapter
from .mock_llm import final_response, tool_call_response


def _round(index):
    balances = {"credits_unclassified": 4199} if index == 1 else {"credits_before_buy": 3200}
    observed = {"agent": "Jett", "weapon": "Vandal", "armor": "light", **balances}
    player = dict(team="ally", slot=1, **observed, evidence=[
        dict(field_name=key, status="user_confirmed", source="user_input") for key in observed])
    return dict(match_id="demo", round_id=f"demo-r{index}", round_index=index, map_id="sunset",
                map_revision="demo-revision", game_patch="demo-patch", ally_players=[player])


def _frame():
    players = [dict(team="ally", slot=1, region="A Site", status="observed"),
               dict(team="ally", slot=2, region="Mid", status="observed")]
    return dict(snapshot_id="demo-frame", round_id="demo-r1", map_id="sunset", map_revision="demo-revision",
                timestamp_ms=2000, coordinate_system="region_only", players=players,
                evidence=[dict(field_name=f"players.ally.{p['slot']}.region", status="observed",
                               source="screenshot", note="合成演示样例") for p in players])


class DemoLlm(ILlmAdapter):
    def __init__(self, task_id, task, snapshots=None):
        self.task_id, self.task = task_id, task
        if snapshots is not None:
            self.snapshots = snapshots
        elif task == "position_review":
            self.snapshots = [_frame()]
        else:
            self.snapshots = [_round(1), _round(2)] if task == "match_review" else [_round(1)]

    async def chat(self, request):
        results = [json.loads(m.text) for m in request.messages if m.role == "tool"]
        if any(r["status"] == "failed" for r in results):
            errors = [err for r in results for err in r["errors"]]
            return final_response(CoachResponse(task_id=self.task_id, task_status="failed",
                                  missing_information=errors, coach_summary="演示工具执行失败。"))
        checks = [r["data"] for r in results if r["tool_name"] == "validate_round_data"]
        if not checks and request.tools:
            field = "position_snapshot" if self.task == "position_review" else "round_snapshot"
            return tool_call_response(*[("validate_round_data", {field: s}) for s in self.snapshots])
        if checks and len(checks) == len(results) and request.tools:
            snapshots = [r["normalized_snapshot"] for r in checks]
            first = snapshots[0]
            if self.task == "position_review":
                calls = [("analyze_positions", dict(position_snapshots=snapshots, map_id=first["map_id"],
                                                    map_revision=first["map_revision"]))]
                region = next((p["region"] for p in first["players"] or [] if p["region"]), None)
                if first["map_revision"] and region:
                    calls.append(("lookup_tactics", dict(map_id=first["map_id"], map_revision=first["map_revision"],
                                                         query=region)))
                return tool_call_response(*calls)
            calls = [("analyze_buy_state", {"round_snapshot": s}) for s in snapshots]
            if self.task == "buy_review":
                if first["game_patch"]:
                    calls.append(("get_game_rules", dict(game_patch=first["game_patch"], rule_categories=["weapons"])))
                calls.append(("simulate_buy_options", dict(round_snapshot=first, strategies=["full_buy", "eco"])))
            return tool_call_response(*calls)
        domain = [r for r in results if r["tool_name"] != "validate_round_data"]
        if not domain:
            return final_response(CoachResponse(task_id=self.task_id, task_status="failed",
                                  coach_summary="工具调用额度不足，演示尚未完成。"))
        return final_response(self._report(domain, checks))

    def _report(self, results, checks):
        missing = list(dict.fromkeys(w for r in results for w in r["warnings"]))
        if any(c["warnings"] or c["missing_fields"] for c in checks):
            missing.append("输入快照含缺失字段或尚未核验的来源。")
        buy, position, evidence, recommendations = [], [], [], []
        for check, result in zip(checks, [r for r in results if r["tool_name"] == "analyze_buy_state"]):
            snap, data = check["normalized_snapshot"], result["data"]
            economy = data["ally_economy"]
            basis = "购买前" if economy["time_basis"] == "before_buy" else "购买后"
            amount = economy["known_credits"]
            guns = "、".join(f"{name} × {n}" for name, n in data["weapon_distribution"]["ally"].items())
            balance = (f"己方{basis}余额未知。" if amount is None else
                       f"已记录己方 {economy['known_players']}/5 人的{basis}余额，合计 {amount} 金币。")
            if economy["estimated"]:
                balance += "合计含估算值。"
            buy.append(f"第 {snap['round_index']} 回合，己方已知主武器：{guns or '未知'}。{balance}")
            for ev in data["evidence"]:
                if ev["field_name"].endswith(("credits_before_buy", "credits_unclassified", "credits_remaining")):
                    evidence.append({**ev, "field_name": f"{snap['round_id']}.{ev['field_name']}"})
            for p in snap["ally_players"] or []:
                if p["credits_unclassified"] is not None:
                    player = p["agent"] or f"己方 {p['slot']} 号位"
                    buy.append(f"{player} 的 {p['credits_unclassified']} 金币时点不明，已从预算中排除。")
                    if not recommendations:
                        recommendations.append(dict(priority=1, action="记录购买前及购买后各一次余额。",
                            reason="时点不明的数值无法用于模拟该回合预算。", expected_benefit="下次复盘可以核对个人花费。"))
        for r in results:
            if r["tool_name"] == "analyze_positions":
                evidence.extend(r["evidence"])
                for frame in r["data"]["region_distribution"]:
                    for region, counts in frame["counts"].items():
                        teams = "、".join(f"{label} {counts[team]} 人" for team, label in
                                         (("ally", "己方"), ("enemy", "敌方")) if team in counts)
                        position.append(f"{frame['snapshot_id']} · {region} 记录到{teams}。")
        summary = (f"已复盘 {len(checks)} 个站位快照。区域人数仅反映已记录的玩家。"
                   if self.task == "position_review" else f"已复盘 {len(checks)} 个回合。")
        return CoachResponse(task_id=self.task_id, task_status="partial" if missing else "success",
                             overall_judgement="离线结构化数据复盘", buy_review=" ".join(buy) or None,
                             position_review=" ".join(position) or None, missing_information=missing,
                             recommendations=recommendations, evidence=evidence, coach_summary=summary)
