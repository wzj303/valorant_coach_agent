"""End-to-end agent flow driven by MockLlmAdapter."""
from __future__ import annotations

import pytest

from agent import (AgentRequest, ImagePart, LlmCapabilityError, LlmTimeoutError, MockLlmAdapter,
                   SkillNotFoundError)
from agent.exceptions import AgentRequestError, InvalidImageError
from agent.mock_llm import final_response, text_response, tool_call_response

from tests.agent.helpers import jett_snapshot, report

TID = "t-1"


def buy_request(png, **kw):
    return AgentRequest(text="帮我看看这回合的购买", images=[png], task_type="buy_review", task_id=TID, **kw)


async def test_image_reaches_the_model_as_pixels_not_a_path(make_loop, png_file):
    loop, llm = make_loop([final_response(report(TID))])
    await loop.run(buy_request(png_file))
    first = llm.requests[0]
    user = [m for m in first.messages if m.role == "user"][0]
    assert len(user.images) == 1
    assert user.images[0].media_type == "image/png" and user.images[0].data
    assert str(png_file) not in user.text and "buy_phase.png" in user.text  # label, not a path


async def test_accepts_bytes_and_prebuilt_imagepart(make_loop, png_bytes):
    loop, llm = make_loop([final_response(report(TID)), final_response(report(TID))])
    await loop.run(AgentRequest(text="x", images=[png_bytes], task_type="buy_review", task_id=TID))
    await loop.run(AgentRequest(text="x", images=[ImagePart.from_bytes(png_bytes)], task_type="buy_review", task_id=TID))
    assert all(r.has_images for r in llm.requests)


async def test_buy_flow_with_multiple_tool_rounds(make_loop, png_bytes):
    steps = [
        tool_call_response(("validate_round_data", {"round_snapshot": jett_snapshot()})),
        tool_call_response(("analyze_buy_state", {"round_snapshot": jett_snapshot()}),
                           ("get_game_rules", {"game_patch": "unknown", "rule_categories": ["economy"]})),
        final_response(report(TID)),
    ]
    loop, llm = make_loop(steps)
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.response.task_status == "partial" and result.response.task_id == TID
    assert [e.tool_name for e in result.trace.tool_executions] == [
        "validate_round_data", "analyze_buy_state", "get_game_rules"]
    # every tool really ran and returned usable (partial) data: nothing silently failed
    assert [e.status for e in result.trace.tool_executions] == ["partial", "partial", "partial"]
    assert all(e.failure_kind is None for e in result.trace.tool_executions)
    # tool results were fed back to the model before the next turn
    tool_msgs = [m for m in llm.requests[-1].messages if m.role == "tool"]
    assert len(tool_msgs) == 3 and all(m.tool_call_id for m in tool_msgs)
    assert result.trace.iterations == 3 and result.trace.tool_calls == 3


async def test_validate_tool_really_runs_gpt2_validator(make_loop, png_bytes):
    loop, _ = make_loop([tool_call_response(("validate_round_data", {"round_snapshot": jett_snapshot()})),
                         final_response(report(TID))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    ex = result.trace.tool_executions[0]
    assert ex.status == "partial"
    assert ex.result["data"]["normalized_snapshot"]["ally_players"][0]["agent"] == "Jett"
    assert ex.result["data"]["completeness"] < 0.5


async def test_tool_failure_is_reported_and_status_downgraded(make_loop, png_bytes):
    bad = {"round_snapshot": {"match_id": "m1"}}  # missing required keys
    loop, llm = make_loop([tool_call_response(("validate_round_data", bad)),
                           final_response(report(TID, task_status="success", missing_information=[]))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.tool_executions[0].status == "failed"
    assert result.response.task_status == "partial"          # model said success; the tool failure wins
    assert any("validate_round_data" in m for m in result.response.missing_information)
    fed_back = [m for m in llm.requests[-1].messages if m.role == "tool"][0]
    assert fed_back.is_error and "round_id" in fed_back.text  # the model is told which field to fix


async def test_recovered_tool_failure_does_not_downgrade(make_loop, png_bytes):
    bad = {"round_snapshot": {"match_id": "m1"}}
    ok = {"round_snapshot": jett_snapshot()}
    ex_ok = dict(report(TID, task_status="success", findings=[], evidence=[], missing_information=[],
                        recommendations=[]).model_dump(mode="json"))
    loop, _ = make_loop([tool_call_response(("validate_round_data", bad)),
                         tool_call_response(("validate_round_data", ok)), final_response(ex_ok)])
    result = await loop.run_with_trace(buy_request(png_bytes))
    statuses = [e.status for e in result.trace.tool_executions]
    assert statuses == ["failed", "partial"]
    assert result.response.task_status == "partial"  # second call is partial, not success


async def test_unknown_tool_does_not_crash(make_loop, png_bytes):
    loop, _ = make_loop([tool_call_response(("rm_rf", {})), final_response(report(TID))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.tool_executions[0].failure_kind == "unknown_tool"


async def test_tool_outside_the_skill_allowlist_is_refused(make_loop, png_bytes):
    # analyze_positions exists in the registry but buy-decision-review does not allow it
    loop, _ = make_loop([tool_call_response(("analyze_positions", {"position_snapshots": [], "map_id": "sunset", "map_revision": None})),
                         final_response(report(TID))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.tool_executions[0].failure_kind == "unknown_tool"


async def test_skill_is_selected_from_task_type_and_injected(make_loop, png_bytes):
    loop, llm = make_loop([final_response(report(TID))])
    await loop.run(AgentRequest(text="x", images=[png_bytes], task_type="position_review", task_id=TID))
    system = llm.requests[0].messages[0].text
    assert "position-coordination-review" in system and "区域里的人数" in system
    names = {t.name for t in llm.requests[0].tools}
    assert names == {"validate_round_data", "analyze_positions", "lookup_tactics", "get_coaching_advice"}


async def test_skill_is_selected_from_text_when_no_task_type(make_loop, png_bytes):
    loop, llm = make_loop([final_response(report(TID))])
    await loop.run(AgentRequest(text="这回合的站位和小地图有没有问题", images=[png_bytes], task_id=TID))
    assert "position-coordination-review" in llm.requests[0].messages[0].text


async def test_model_chooses_skill_when_request_is_ambiguous(make_loop, png_bytes):
    loop, llm = make_loop([tool_call_response(("load_skill", {"name": "buy-decision-review"})),
                           final_response(report(TID))])
    result = await loop.run_with_trace(AgentRequest(text="看看这张图", images=[png_bytes], task_id=TID))
    assert [t.name for t in llm.requests[0].tools] == ["load_skill"]
    assert "buy-decision-review" in llm.requests[1].messages[0].text
    assert result.trace.skill == "buy-decision-review"
    assert "validate_round_data" in {t.name for t in llm.requests[1].tools}


async def test_unknown_task_type_raises(make_loop, png_bytes):
    loop, _ = make_loop([])
    with pytest.raises(SkillNotFoundError):
        await loop.run(AgentRequest(text="x", images=[png_bytes], task_type="nonsense", task_id=TID))


async def test_invalid_report_is_repaired(make_loop, png_bytes):
    loop, llm = make_loop([text_response("这是分析，没有 JSON"),
                           text_response('{"task_id": "t-1", "task_status": "bogus", "coach_summary": "x"}'),
                           final_response(report(TID))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.repairs == 2 and result.response.task_status == "partial"
    repair_prompt = llm.requests[2].messages[-1].text
    assert "task_status" in repair_prompt  # tells the model exactly what was wrong


async def test_never_valid_report_yields_failed_response_not_an_exception(make_loop, png_bytes):
    loop, _ = make_loop([text_response("nope")] * 3, max_repairs=2)
    resp = await loop.run(buy_request(png_bytes))
    assert resp.task_status == "failed" and resp.task_id == TID and resp.missing_information


async def test_json_in_code_fence_and_wrong_task_id_are_tolerated(make_loop, png_bytes):
    body = report("model-made-up-id").model_dump_json()
    loop, _ = make_loop([text_response(f"好的，报告如下：\n```json\n{body}\n```")])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.response.task_id == TID
    assert any("task_id" in n for n in result.trace.notes)


async def test_more_than_three_recommendations_are_trimmed_by_priority(make_loop, png_bytes):
    data = report(TID).model_dump(mode="json")
    data["recommendations"] = [{"priority": p, "action": f"a{p}", "reason": "r", "expected_benefit": "b"}
                               for p in (4, 2, 1, 3)]
    loop, _ = make_loop([final_response(data)])
    resp = await loop.run(buy_request(png_bytes))
    assert [r.priority for r in resp.recommendations] == [1, 2, 3]


async def test_tool_call_budget_is_enforced(make_loop, png_bytes):
    call = tool_call_response(("validate_round_data", {"round_snapshot": jett_snapshot()}))
    loop, llm = make_loop([call, call, call, final_response(report(TID))], max_tool_calls=2)
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.tool_calls == 2
    last_tools = llm.requests[-1].tools
    assert last_tools == []  # budget gone: the model can no longer call tools


async def test_iteration_cap_forces_a_final_answer(make_loop, png_bytes):
    call = tool_call_response(("validate_round_data", {"round_snapshot": jett_snapshot()}))
    loop, llm = make_loop([call, call, final_response(report(TID))], max_iterations=2)
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.response.task_status == "partial"
    assert llm.requests[-1].tools == [] and any("max_iterations" in n for n in result.trace.notes)


async def test_missing_tool_is_announced_to_the_model(skills, png_bytes, monkeypatch):
    from agent import AgentLoop
    from tools.registry import build_registry
    monkeypatch.setattr("tools.registry.load_domain_handler", lambda name: None)
    llm = MockLlmAdapter([final_response(report(TID))])
    loop = AgentLoop(llm, build_registry(), skills)   # no domain tools registered at all
    await loop.run(buy_request(png_bytes))
    system = llm.requests[0].messages[0].text
    assert "analyze_buy_state" in system and "没有注册" in system
    assert {t.name for t in llm.requests[0].tools} == {"validate_round_data"}


async def test_llm_errors_propagate_as_typed_errors(make_loop, png_bytes):
    loop, _ = make_loop([LlmTimeoutError("slow")])
    with pytest.raises(LlmTimeoutError):
        await loop.run(buy_request(png_bytes))


async def test_empty_request_rejected(make_loop):
    loop, _ = make_loop([])
    with pytest.raises(AgentRequestError):
        await loop.run(AgentRequest(text="  ", task_type="buy_review"))


async def test_bad_image_rejected_before_calling_the_model(make_loop):
    loop, llm = make_loop([])
    with pytest.raises(InvalidImageError):
        await loop.run(AgentRequest(text="x", images=[b"not an image"], task_type="buy_review"))
    assert llm.requests == []


async def test_vision_less_adapter_rejects_images_early(skills, registry, png_bytes):
    from agent import AgentLoop
    llm = MockLlmAdapter([final_response(report(TID))], supports_vision=False)
    with pytest.raises(LlmCapabilityError):
        await AgentLoop(llm, registry, skills).run(buy_request(png_bytes))
    assert llm.requests == []


async def test_text_only_request_works(make_loop):
    loop, _ = make_loop([final_response(report(TID))])
    resp = await loop.run(AgentRequest(text="经济局该怎么买？", task_type="buy_review", task_id=TID))
    assert resp.task_id == TID


async def test_usage_is_accumulated(make_loop, png_bytes):
    loop, _ = make_loop([tool_call_response(("validate_round_data", {"round_snapshot": jett_snapshot()})),
                         final_response(report(TID))])
    result = await loop.run_with_trace(buy_request(png_bytes))
    assert result.trace.usage.input_tokens == 20 and result.trace.usage.output_tokens == 10
