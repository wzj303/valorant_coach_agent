"""Full-match behavior: real graph + SQLite + domain tools, with only the LLM scripted."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from agent import ILlmAdapter, LlmRequest, LlmResponse
from agent.exceptions import AgentRequestError, LlmProviderError
from agent.match_service import MatchConflictError, MatchService
from schemas import CoachResponse
from tests.agent.helpers import jett_snapshot, make_png


def snapshot(index, credits=3200):
    value = jett_snapshot()
    value.update(round_index=index, round_id=f"m1-r{index}", score={"ally": index - 1, "enemy": 0},
                 side="attack" if index <= 12 else "defense")
    player = value["ally_players"][0]
    player.pop("credits_unclassified")
    player["credits_before_buy"] = credits
    return value


def inputs(*indices):
    return [{"round_index": index, "snapshot": snapshot(index)} for index in indices]


def image_inputs(*indices):
    encoded = base64.b64encode(make_png()).decode()
    return [{"round_index": index, "images": [{"name": f"r{index}.png", "data": encoded}]} for index in indices]


def service(tmp_path):
    return MatchService(tmp_path / "matches.sqlite", tmp_path / "images")


class MatchLlm(ILlmAdapter):
    def __init__(self, *, fail_review=None, bad_extractions=0):
        self.requests = []
        self.extractions = []
        self.reviews = []
        self.summaries = []
        self.fail_review = fail_review
        self.bad_extractions = bad_extractions

    async def chat(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if "round_snapshot" in request.response_schema.get("properties", {}):
            data = json.loads(request.messages[-1].text)
            index = data["round_index"]
            self.extractions.append(index)
            assert len(request.messages[-1].images) == 1
            assert len(data["previous_round_indices"]) == index - 1
            if self.bad_extractions:
                self.bad_extractions -= 1
                return LlmResponse(text='{"round_snapshot": {"bad": true}}')
            result = {"round_snapshot": snapshot(index), "position_snapshot": None}
        else:
            assert not request.has_images
            data = json.loads(request.messages[-1].text.rsplit("\n\n", 1)[-1])
            task_id = request.messages[-1].text.splitlines()[0].removeprefix("task_id: ")
            if "current_round" in data:
                index = data["current_round"]["round_index"]
                self.reviews.append(index)
                assert len(data["prior_rounds"]) == index - 1
                assert all(row["round_index"] < index for row in data["prior_rounds"])
                if index == self.fail_review:
                    self.fail_review = None
                    raise LlmProviderError("temporary provider failure")
            else:
                self.summaries.append(data)
            result = CoachResponse(task_id=task_id, task_status="success", coach_summary="基于已分析回合的复盘。"
                                   ).model_dump(mode="json")
        return LlmResponse(text=json.dumps(result))


async def test_thirteen_opening_images_receive_sequential_context_and_persist(tmp_path):
    llm = MatchLlm()
    result = await service(tmp_path).run("full-match", image_inputs(*range(1, 14)), llm=llm)
    assert result["completed_rounds"] == 13
    assert llm.extractions == llm.reviews == list(range(1, 14))
    assert len(llm.summaries) == 1
    assert len(llm.summaries[0]["timeline"]) == 13
    assert result["timeline"][1]["continuity"]["previous_round_outcome"] == "ally_win"
    assert result["timeline"][12]["continuity"]["side_changed"]
    assert result["report"]["task_status"] == "partial"  # unverified rules + opening-only coverage
    assert not result["can_resume"]
    # A separate service instance (as after a process restart) reads the same checkpoints.
    restored = await service(tmp_path).status("full-match")
    assert restored["timeline"] == result["timeline"]
    assert restored["report"] == result["report"]
    assert "base64" not in json.dumps(restored)


async def test_interrupt_resume_does_not_reextract_or_recompute_saved_nodes(tmp_path):
    llm = MatchLlm(fail_review=2)
    first = service(tmp_path)
    with pytest.raises(LlmProviderError):
        await first.run("resume-test", image_inputs(1, 2, 3), llm=llm)
    status = await service(tmp_path).status("resume-test")
    assert status["completed_rounds"] == 1 and status["can_resume"]
    assert status["current_round"] == 2
    with pytest.raises(MatchConflictError, match="interrupted"):
        await first.run("resume-test", image_inputs(4), llm=llm)
    result = await service(tmp_path).run("resume-test", [], llm=llm, resume=True)
    assert result["completed_rounds"] == 3
    assert llm.extractions == [1, 2, 3]
    assert llm.reviews == [1, 2, 2, 3]


async def test_invalid_round_is_recorded_without_tactical_calculations(tmp_path):
    rounds = inputs(1, 2, 3)
    rounds[1]["snapshot"] = {"round_index": 2}
    result = await service(tmp_path).run("bad-round", rounds)
    assert result["completed_rounds"] == 3 and result["failed_rounds"] == [2]
    assert result["rounds"][1]["round_snapshot"] is None
    assert "未执行战术计算" in result["rounds"][1]["report"]["coach_summary"]
    assert result["timeline"][2]["continuity"]["gap"]
    assert result["report"]["task_status"] == "partial"


async def test_same_batch_is_idempotent_and_append_preserves_earlier_reports(tmp_path):
    instance = service(tmp_path)
    first = await instance.run("append-test", inputs(1, 2), summarize=False)
    again = await instance.run("append-test", inputs(1, 2), summarize=False)
    assert first["rounds"] == again["rounds"]
    assert first["timeline"] == again["timeline"]
    later = await service(tmp_path).run("append-test", inputs(3, 4))
    assert later["rounds"][:2] == first["rounds"]
    assert later["completed_rounds"] == 4


async def test_gap_fill_and_correction_recompute_later_context(tmp_path):
    instance = service(tmp_path)
    first = await instance.run("corrections", inputs(1, 3))
    assert first["timeline"][1]["continuity"]["gap"]
    filled = await instance.run("corrections", inputs(2))
    assert [row["round_index"] for row in filled["timeline"]] == [1, 2, 3]
    assert not filled["timeline"][2]["continuity"].get("gap")
    changed = [{"round_index": 2, "snapshot": snapshot(2, 4500)}]
    with pytest.raises(MatchConflictError, match="already exists"):
        await instance.run("corrections", changed)
    corrected = await instance.run("corrections", changed, replace_existing=True)
    assert corrected["timeline"][1]["ally_economy"]["known_credits"] == 4500
    assert corrected["rounds"][0] == filled["rounds"][0]
    assert corrected["completed_rounds"] == 3


async def test_extraction_validation_retries_are_bounded(tmp_path):
    llm = MatchLlm(bad_extractions=10)
    result = await service(tmp_path).run("retries", image_inputs(1), llm=llm)
    assert llm.extractions == [1, 1, 1]
    assert not llm.reviews
    assert result["failed_rounds"] == [1]
    assert result["report"]["task_status"] == "failed"


async def test_opening_score_round_mismatch_does_not_reach_domain_tools(tmp_path):
    item = inputs(1)[0]
    item["snapshot"]["score"] = {"ally": 3, "enemy": 2}
    result = await service(tmp_path).run("wrong-order", [item])
    assert result["failed_rounds"] == [1]
    assert "比分之和" in result["rounds"][0]["report"]["missing_information"][0]


async def test_foreign_position_snapshot_is_not_silently_reassigned(tmp_path):
    item = inputs(1)[0]
    item["position_snapshot"] = {"snapshot_id": "foreign", "round_id": "another-round", "map_id": "sunset",
                                 "map_revision": None, "players": [], "coordinate_system": "region_only"}
    result = await service(tmp_path).run("foreign-position", [item])
    assert result["failed_rounds"] == [1]
    assert "不能合并不同回合" in result["rounds"][0]["report"]["missing_information"][0]


async def test_round_evidence_is_namespaced_before_match_summary(tmp_path):
    class EvidenceLlm(MatchLlm):
        async def chat(self, request):
            response = await super().chat(request)
            data = json.loads(response.text)
            if "task_id" in data:
                data.update(evidence=[{"field_name": "ally.balance", "status": "derived", "source": "tool"}],
                            findings=[{"title": "经济观察", "severity": "info", "explanation": "参见工具数据。",
                                       "evidence_refs": ["ally.balance"]}])
            return LlmResponse(text=json.dumps(data))

    result = await service(tmp_path).run("evidence", inputs(1, 2), llm=EvidenceLlm())
    assert result["rounds"][0]["report"]["evidence"][0]["field_name"] == "evidence-r1.ally.balance"
    assert result["rounds"][1]["report"]["findings"][0]["evidence_refs"] == ["evidence-r2.ally.balance"]


async def test_offline_cannot_claim_to_read_screenshots_and_modes_cannot_mix(tmp_path):
    instance = service(tmp_path)
    with pytest.raises(AgentRequestError, match="视觉模型"):
        await instance.run("no-key", image_inputs(1))
    await instance.run("no-key", inputs(1))
    with pytest.raises(MatchConflictError, match="cannot mix"):
        await instance.run("no-key", inputs(2), llm=MatchLlm())


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 65, 42])
async def test_match_identifiers_cannot_escape_storage(tmp_path, bad):
    with pytest.raises(AgentRequestError, match="match_id"):
        await service(tmp_path).run(bad, inputs(1))


async def test_duplicates_and_boolean_round_numbers_are_rejected(tmp_path):
    instance = service(tmp_path)
    with pytest.raises(AgentRequestError, match="duplicate"):
        await instance.run("duplicate", inputs(1, 1))
    with pytest.raises(AgentRequestError):
        await instance.run("boolean", [{"round_index": True, "snapshot": snapshot(1)}])


async def test_concurrent_requests_for_one_match_are_rejected(tmp_path):
    instance = service(tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()

    class WaitingLlm(MatchLlm):
        async def chat(self, request):
            started.set()
            await finish.wait()
            return await super().chat(request)

    llm = WaitingLlm()
    task = asyncio.create_task(instance.run("busy", image_inputs(1), llm=llm))
    await asyncio.wait_for(started.wait(), 10)
    try:
        assert (await instance.status("busy"))["status"] == "running"
        with pytest.raises(MatchConflictError, match="already being analyzed"):
            await instance.run("busy", inputs(2), llm=llm)
    finally:
        finish.set()
        await task
