"""LangGraph workflow: extract -> validate -> analyze -> review -> save -> next.

Only validated snapshots reach domain tools. Every round is checkpointed separately;
the summary consumes structured round records, never the whole match's screenshots.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from schemas import CoachResponse

from .agent_loop import AgentLoop, AgentRequest, AgentTrace
from .exceptions import LlmCapabilityError
from .llm_adapter import ILlmAdapter
from .llm_types import ImagePart, LlmRequest, Message, ToolCall
from .match_types import MatchState, RoundObservation
from .report import guide_recommendation
from .skill_loader import SkillLoader
from .tool_registry import ToolExecution, ToolRegistry
from .utils import compact_json, extract_json_object, strip_titles


def timeline(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        snapshot = record.get("round_snapshot") or {}
        economy = next((tool["result"].get("data") for tool in record.get("tools", [])
                        if tool["tool_name"] == "analyze_buy_state"), None) or {}
        rows.append({"round_index": record["round_index"], "round_id": snapshot.get("round_id"),
                     "status": record["report"]["task_status"], "side": snapshot.get("side"),
                     "score": snapshot.get("score"), "ally_economy": economy.get("ally_economy"),
                     "enemy_economy": economy.get("enemy_economy"),
                     "weapon_distribution": economy.get("weapon_distribution"),
                     "continuity": record.get("continuity", {}),
                     "coach_summary": record["report"]["coach_summary"]})
    return rows


def continuity(records: list[dict], snapshot: dict, tools: list[dict]) -> dict:
    """Compare adjacent openings without inventing spending, income or the final outcome."""
    result: dict[str, Any] = {"previous_round_outcome": None, "opening_balance_change": None}
    if not records:
        return result
    previous = records[-1]
    old = previous.get("round_snapshot") or {}
    if old.get("round_index", -1) + 1 != snapshot["round_index"]:
        result["gap"] = True
        return result
    result["side_changed"] = bool(old.get("side") and snapshot.get("side")
                                  and old["side"] != snapshot["side"])
    before, after = old.get("score") or {}, snapshot.get("score") or {}
    if all(isinstance(score.get(team), int) for score in (before, after) for team in ("ally", "enemy")):
        delta = (after["ally"] - before["ally"], after["enemy"] - before["enemy"])
        if delta in ((1, 0), (0, 1)):
            result["previous_round_outcome"] = "ally_win" if delta[0] else "enemy_win"
            result["outcome_source"] = "next_opening_score"
        else:
            result["score_warning"] = "相邻开局比分无法确认上一回合胜负；请核对回合顺序和队伍视角。"
    old_economy = next((tool["result"].get("data") for tool in previous.get("tools", [])
                        if tool["tool_name"] == "analyze_buy_state"), None) or {}
    new_economy = next((tool["result"].get("data") for tool in tools
                        if tool["tool_name"] == "analyze_buy_state"), None) or {}
    a, b = old_economy.get("ally_economy") or {}, new_economy.get("ally_economy") or {}
    if (a.get("complete") and b.get("complete") and not a.get("estimated") and not b.get("estimated")
            and a.get("time_basis") == b.get("time_basis") and old.get("side") == snapshot.get("side")
            and old.get("side") is not None):
        result["opening_balance_change"] = b["known_credits"] - a["known_credits"]
        result["balance_note"] = "同一攻防方向、同一余额时点的开局余额变化，不代表收入或花费。"
    return result


class MatchWorkflow:
    def __init__(self, llm: ILlmAdapter | None, registry: ToolRegistry, skills: SkillLoader,
                 checkpointer: Any, media_dir: Path):
        self.llm, self.registry, self.skills, self.media_dir = llm, registry, skills, media_dir
        graph = StateGraph(MatchState)
        for name in ("select_round", "extract", "validate", "analyze", "review", "save_round", "summarize"):
            graph.add_node(name, getattr(self, "_" + name))
        graph.add_edge(START, "select_round")
        graph.add_conditional_edges("select_round", self._next_round,
                                    {"extract": "extract", "summarize": "summarize", "end": END})
        graph.add_edge("extract", "validate")
        graph.add_conditional_edges("validate", self._after_validation,
                                    {"retry": "extract", "analyze": "analyze", "failed": "save_round"})
        graph.add_edge("analyze", "review")
        graph.add_edge("review", "save_round")
        graph.add_edge("save_round", "select_round")
        graph.add_edge("summarize", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    def _select_round(self, state: MatchState) -> dict:
        cursor = state["cursor"]
        if cursor >= len(state["pending"]):
            return {"phase": "summarizing" if state["summarize"] else "ready", "current": {}}
        return {"phase": "extracting", "current": {"input": state["pending"][cursor], "attempts": 0,
                                                    "tools": [], "errors": [], "trace": {}}}

    @staticmethod
    def _next_round(state: MatchState) -> str:
        if state["cursor"] < len(state["pending"]):
            return "extract"
        return "summarize" if state["summarize"] and state["records"] else "end"

    async def _extract(self, state: MatchState) -> dict:
        current = dict(state["current"])
        item = current["input"]
        current["attempts"] += 1
        current["candidate"] = None
        if item.get("snapshot"):
            current["candidate"] = {"round_snapshot": item["snapshot"],
                                    "position_snapshot": item.get("position_snapshot")}
            return {"current": current, "phase": "validating"}
        if self.llm is None:
            current["errors"] = ["离线整场复盘只接受结构化快照；截图识别需要配置视觉模型。"]
            return {"current": current, "phase": "validating"}
        if not self.llm.supports_vision:
            raise LlmCapabilityError("match screenshot extraction requires a vision model")
        images = []
        for image in item["images"]:
            image_path = (self.media_dir / image["path"]).resolve()
            if self.media_dir.resolve() not in image_path.parents:
                raise ValueError("image reference is outside the match media directory")
            images.append(ImagePart.from_file(image_path).model_copy(update={"label": image["name"]}))
        prompt = (
            "你负责读取本回合开局截图，只提取可见事实，不作战术结论。只输出 RoundObservation JSON。"
            "round_snapshot 必填，position_snapshot 仅在小地图可读时填写，否则 null。"
            "未知字段必须 null，不得从上一回合余额、装备或胜负补齐。地图仅支持 sunset，其他地图不能伪装成 sunset。"
            "队伍视角保持一致：ally 为用户一方，换边不交换 ally/enemy。"
            "购买前和购买后余额无法区分时填 credits_unclassified。所有已读字段附 evidence，"
            "直接观察为 observed/screenshot，用户确认才用 user_confirmed/user_input。"
            "不要根据武器推断是否在本回合买入，不要推断回合结束、移动轨迹和未观察到的敌人。\n"
            + self.skills.render(self.skills.get("buy-decision-review")) + "\n"
            + self.skills.render(self.skills.get("position-coordination-review")))
        facts = {"match_id": state["match_id"], "round_id": f"{state['match_id']}-r{item['round_index']}",
                 "round_index": item["round_index"], "question": state["text"],
                 "round_notes": item.get("text", ""), "user_observations": state["observations"],
                 "previous_round_indices": [r["round_index"] for r in state["records"]],
                 "previous_errors": current.get("errors", [])}
        reply = await self.llm.chat(LlmRequest(messages=[Message.system(prompt),
                                   Message.user(compact_json(facts), images)],
                                   response_schema=strip_titles(RoundObservation.model_json_schema())))
        trace = AgentTrace.model_validate(current.get("trace") or {"task_id": facts["round_id"]})
        trace.usage.add(reply.usage)
        trace.iterations += 1
        current["trace"] = trace.model_dump(mode="json")
        candidate = extract_json_object(reply.text)
        current["candidate"] = candidate
        current["errors"] = [] if candidate else ["模型未返回可解析的 RoundObservation JSON。"]
        return {"current": current, "phase": "validating"}

    async def _execute(self, name: str, arguments: dict) -> dict:
        execution = await self.registry.execute(ToolCall(id=f"match-{name}", name=name, arguments=arguments))
        return execution.model_dump(mode="json")

    async def _validate(self, state: MatchState) -> dict:
        current = dict(state["current"])
        candidate = current.get("candidate")
        current["validated"] = False
        try:
            if not candidate:
                raise ValueError("; ".join(current["errors"]) or "没有可校验的截图数据。")
            observation = RoundObservation.model_validate_json(compact_json(candidate))
            if observation.position_snapshot is not None \
                    and observation.position_snapshot.round_id != observation.round_snapshot.round_id:
                raise ValueError("站位快照与回合快照的 round_id 不一致，不能合并不同回合。")
            snapshot = observation.round_snapshot.model_dump(mode="json")
            item = current["input"]
            if snapshot["round_index"] != item["round_index"]:
                raise ValueError("截图/快照回合编号与上传时指定的回合编号不同，请核对顺序。")
            score = snapshot.get("score") or {}
            if all(score.get(team) is not None for team in ("ally", "enemy")) \
                    and score["ally"] + score["enemy"] != item["round_index"] - 1:
                raise ValueError("开局比分之和与回合编号不一致。请核对截图顺序，不能为匹配编号而改写可见比分。")
            # These are host identifiers, not visual observations; preserve all game facts.
            snapshot.update(match_id=state["match_id"], round_id=f"{state['match_id']}-r{item['round_index']}")
            if item["images"]:
                snapshot["source_images"] = [image["name"] for image in item["images"]]
            position = observation.position_snapshot.model_dump(mode="json") if observation.position_snapshot else None
            if position:
                position.update(round_id=snapshot["round_id"], snapshot_id=f"{snapshot['round_id']}-opening")
            execution = await self._execute("validate_round_data", {"round_snapshot": snapshot,
                                                                    "position_snapshot": position})
            current["tools"] = [execution]
            result = execution["result"]
            if result["status"] == "failed":
                raise ValueError("; ".join(result["errors"]))
            current["round_snapshot"] = result["data"]["normalized_snapshot"]
            current["position_snapshot"] = result["data"].get("normalized_position_snapshot")
            current["validated"] = True
            current["errors"] = []
        except (ValidationError, ValueError) as exc:
            current["errors"] = [str(exc)[:4000]]
        return {"current": current, "phase": "analyzing" if current["validated"] else "validation_failed"}

    @staticmethod
    def _after_validation(state: MatchState) -> str:
        current = state["current"]
        if current["validated"]:
            return "analyze"
        if not current["input"].get("snapshot") and current["input"]["images"] and current["attempts"] < 3:
            return "retry"
        return "failed"

    async def _analyze(self, state: MatchState) -> dict:
        current = dict(state["current"])
        snapshot = current["round_snapshot"]
        tools = list(current["tools"])
        calls = [("analyze_buy_state", {"round_snapshot": snapshot})]
        if snapshot.get("game_patch"):
            calls.append(("get_game_rules", {"game_patch": snapshot["game_patch"],
                                              "rule_categories": ["economy", "weapons", "armor"]}))
        if any(p.get("credits_before_buy") is not None for p in snapshot.get("ally_players") or []):
            calls.append(("simulate_buy_options", {"round_snapshot": snapshot,
                                                   "strategies": ["full_buy", "half_buy", "eco"]}))
        position = current.get("position_snapshot")
        if position:
            calls.append(("analyze_positions", {"position_snapshots": [position], "map_id": snapshot["map_id"],
                                                "map_revision": snapshot["map_revision"]}))
        if snapshot.get("map_revision"):
            calls.append(("lookup_tactics", {"map_id": snapshot["map_id"],
                                             "map_revision": snapshot["map_revision"],
                                             "side": snapshot.get("side"), "query": "开局经济与团队配合"}))
        topic = current["input"].get("text", "").strip() or state.get("text", "").strip()
        default_topic = "手枪局买什么" if snapshot["round_index"] in (1, 13) else "队内发枪与混起"
        calls.append(("get_coaching_advice", {"query": topic[:240] or default_topic, "limit": 2}))
        for name, arguments in calls:
            tools.append(await self._execute(name, arguments))
        current["tools"] = tools
        current["continuity"] = continuity(state["records"], snapshot, tools)
        return {"current": current, "phase": "reviewing"}

    @staticmethod
    def _missing(current: dict) -> list[str]:
        missing = list(current.get("errors", []))
        for execution in current.get("tools", []):
            result = execution["result"]
            missing += result.get("warnings", []) + result.get("errors", [])
            if execution["tool_name"] == "validate_round_data" and result.get("data"):
                missing += result["data"].get("missing_fields", [])
        if current.get("continuity", {}).get("score_warning"):
            missing.append(current["continuity"]["score_warning"])
        return list(dict.fromkeys(missing))

    async def _review(self, state: MatchState) -> dict:
        current = dict(state["current"])
        round_id = current["round_snapshot"]["round_id"]
        trace = AgentTrace.model_validate(current.get("trace") or {"task_id": round_id})
        trace.tool_executions = [ToolExecution.model_validate(tool) for tool in current["tools"]]
        trace.tool_calls = len(trace.tool_executions)
        if self.llm is None:
            rows = timeline([{**current, "round_index": current["input"]["round_index"],
                              "report": {"task_status": "partial", "coach_summary": ""}}])
            advice = next((tool["result"] for tool in current["tools"]
                           if tool["tool_name"] == "get_coaching_advice"), {})
            report = self._demo_report(state["match_id"], rows, self._missing(current), advice)
            report = report.model_copy(update={"task_id": round_id})
        else:
            evidence = {"current_round": current["round_snapshot"],
                        "current_positions": current.get("position_snapshot"),
                        "tools": [tool["result"] for tool in current["tools"]],
                        "continuity": current["continuity"], "prior_rounds": timeline(state["records"]),
                        "prior_findings": [{"round_index": record["round_index"],
                                            "findings": record["report"]["findings"]}
                                           for record in state["records"]]}
            request = AgentRequest(task_id=round_id, task_type="match_review", text=(state["text"] +
                f"\n请复盘第 {current['input']['round_index']} 回合开局，并结合此前回合解释连续变化。"
                "只能把 next_opening_score 作为上一回合胜负依据；开局余额差不是花费或收入；"
                "换边和回合缺口要单独说明。截图没有展示的回合内过程、移动和最终胜负均未知。"),
                user_observations=state["observations"] + [current["input"].get("text", "")])
            result = await AgentLoop(self.llm, self.registry, self.skills).report_from_evidence(
                request, compact_json(evidence), trace)
            report, trace = result.response, result.trace
        missing = list(dict.fromkeys(report.missing_information + self._missing(current)))
        status = report.task_status
        if missing and status == "success":
            status = "partial"
        # Evidence paths remain unambiguous when reports from many rounds are combined.
        names = {ev.field_name: ev.field_name if ev.field_name.startswith(round_id + ".")
                 else round_id + "." + ev.field_name for ev in report.evidence}
        evidence = {names[ev.field_name]: ev.model_copy(update={"field_name": names[ev.field_name]})
                    for ev in report.evidence}
        findings = [finding.model_copy(update={"evidence_refs": [names[ref] for ref in finding.evidence_refs]})
                    for finding in report.findings]
        current["report"] = report.model_copy(update={"task_status": status,
                "missing_information": missing, "evidence": list(evidence.values()),
                "findings": findings}).model_dump(mode="json")
        current["trace"] = trace.model_dump(mode="json")
        return {"current": current, "phase": "saving_round"}

    def _save_round(self, state: MatchState) -> dict:
        current = dict(state["current"])
        index = current["input"]["round_index"]
        if not current.get("report"):
            current["report"] = CoachResponse(task_id=f"{state['match_id']}-r{index}", task_status="failed",
                missing_information=self._missing(current), coach_summary=f"第 {index} 回合数据校验失败，未执行战术计算。"
                ).model_dump(mode="json")
        record = {key: current.get(key) for key in ("input", "round_snapshot", "position_snapshot", "tools",
                                                   "report", "continuity", "trace")}
        record["round_index"] = index
        records = state["records"] + [record]
        return {"records": records, "timeline": timeline(records), "cursor": state["cursor"] + 1,
                "current": {}, "phase": "round_saved"}

    @staticmethod
    def _demo_report(match_id: str, rows: list[dict], missing: list[str], advice: dict) -> CoachResponse:
        lines = []
        for row in rows:
            economy = row.get("ally_economy") or {}
            amount = economy.get("known_credits")
            balance = "已知余额未知" if amount is None else (
                f"{'购买前' if economy.get('time_basis') == 'before_buy' else '购买后'}"
                f"已知余额合计 {amount}（{economy.get('known_players', 0)}/5 人）")
            lines.append(f"第 {row['round_index']} 回合：{balance}。")
        recommendation = guide_recommendation(advice)
        return CoachResponse(task_id=match_id, task_status="partial" if missing else "success",
                             recommendations=[recommendation] if recommendation else [],
                             overall_judgement="离线结构化数据连续复盘（未识别截图）",
                             buy_review="\n".join(lines), missing_information=list(dict.fromkeys(missing)),
                             coach_summary=f"已按顺序复盘 {len(rows)} 个开局；仅依据提供的快照与工具结果。")

    async def _summarize(self, state: MatchState) -> dict:
        records = state["records"]
        rows = timeline(records)
        missing = []
        indices = {record["round_index"] for record in records}
        gaps = sorted(set(range(1, max(indices) + 1)) - indices)
        if gaps:
            missing.append("缺少开局截图的回合：" + ", ".join(map(str, gaps)))
        if len(records) < 13:
            missing.append(f"目前仅有 {len(records)} 个开局，只评价已分析回合，不能代表整场。")
        failed = [record["round_index"] for record in records if record["report"]["task_status"] == "failed"]
        if failed:
            missing.append("未能完成分析的回合：" + ", ".join(map(str, failed)))
        if any(record["report"]["task_status"] != "success" for record in records):
            missing.append("部分回合数据或规则不完整，请结合逐回合报告的缺失信息阅读结论。")
        missing.append("仅有开局截图，最后一个已上传回合的结果及完整回合内过程未知。")
        trace = AgentTrace(task_id=state["match_id"], skill="coach-feedback")
        for record in records:
            t = record.get("trace") or {}
            trace.iterations += t.get("iterations", 0)
            trace.repairs += t.get("repairs", 0)
            trace.usage.input_tokens += t.get("usage", {}).get("input_tokens", 0)
            trace.usage.output_tokens += t.get("usage", {}).get("output_tokens", 0)
            trace.tool_executions.extend(ToolExecution.model_validate(tool) for tool in record["tools"])
        advice = await self._execute("get_coaching_advice", {"query": "识别重复习惯与临场调整", "section": "rules", "limit": 1})
        trace.tool_executions.append(ToolExecution.model_validate(advice))
        missing.extend(advice["result"]["warnings"] + advice["result"]["errors"])
        trace.tool_calls = len(trace.tool_executions)
        if self.llm is None:
            report = self._demo_report(state["match_id"], rows, missing, advice["result"])
        else:
            payload = {"timeline": rows, "coaching_advice": advice["result"],
                       "round_reports": [record["report"] for record in records],
                       "coverage": {"uploaded_rounds": len(records), "missing_rounds": gaps, "failed_rounds": failed}}
            request = AgentRequest(task_id=state["match_id"], task_type="match_review", text=state["text"] +
                "\n请生成整场连续复盘：按时间解释经济与购买变化，区分换边阶段，总结重复问题及最多三条训练建议。"
                "重复问题必须引用至少两个不同回合；保留 round_id 证据前缀。只分析已上传回合，"
                "不把开局余额变化当作收入或花费，不从开局图编造对枪、移动、击杀或最后一局结果。",
                user_observations=state["observations"])
            result = await AgentLoop(self.llm, self.registry, self.skills).report_from_evidence(
                request, compact_json(payload), trace)
            report, trace = result.response, result.trace
        report_missing = list(dict.fromkeys(report.missing_information + missing))
        status = "failed" if len(failed) == len(records) else (
            "partial" if report_missing and report.task_status == "success" else report.task_status)
        report = report.model_copy(update={"task_status": status, "missing_information": report_missing})
        return {"report": report.model_dump(mode="json"), "timeline": rows,
                "trace": trace.model_dump(mode="json"), "phase": "ready"}
