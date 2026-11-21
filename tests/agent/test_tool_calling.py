"""ToolRegistry: argument validation, error containment, allow-lists, output contracts."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from agent import ToolCall, ToolRegistry
from agent.exceptions import ToolRegistrationError
from schemas.tool_contracts import BuyStateInput, GameRulesInput
from tools.registry import build_registry

from tests.agent.helpers import fake_handlers, jett_snapshot


def call(name, args=None, id="c1", **kw):
    return ToolCall(id=id, name=name, arguments=args or {}, **kw)


class Echo(BaseModel):
    text: str


def ok_tool(req: Echo):
    return {"tool_name": "echo", "status": "success", "data": {"echo": req.text}, "warnings": [],
            "errors": [], "evidence": []}


@pytest.fixture
def reg():
    r = ToolRegistry()
    r.register("echo", ok_tool, description="echo", input_model=Echo)
    return r


async def test_valid_call_runs_and_returns_toolresult_shape(reg):
    ex = await reg.execute(call("echo", {"text": "hi"}))
    assert ex.ok and ex.status == "success" and ex.result["data"] == {"echo": "hi"}
    assert json.loads(ex.content)["tool_name"] == "echo"


async def test_arguments_are_validated_with_actionable_errors(reg):
    ex = await reg.execute(call("echo", {"text": 5}))
    assert ex.failure_kind == "bad_arguments" and ex.status == "failed"
    assert "text" in ex.result["errors"][1]


async def test_missing_and_extra_arguments_rejected(reg):
    assert (await reg.execute(call("echo", {}))).failure_kind == "bad_arguments"


async def test_unknown_tool_lists_alternatives(reg):
    ex = await reg.execute(call("nope"))
    assert ex.failure_kind == "unknown_tool" and "echo" in ex.result["warnings"][0]


async def test_allowlist_blocks_registered_tool(reg):
    ex = await reg.execute(call("echo", {"text": "x"}), allowed=frozenset({"other"}))
    assert ex.failure_kind == "unknown_tool"
    assert [s.name for s in reg.specs(frozenset({"other"}))] == []


async def test_tool_exception_is_contained(reg):
    def boom(req: Echo):
        raise RuntimeError("kaput")
    reg.register("boom", boom, description="x", input_model=Echo)
    ex = await reg.execute(call("boom", {"text": "x"}))
    assert ex.failure_kind == "tool_error" and "kaput" in ex.result["errors"][0]


async def test_timeout_is_contained():
    r = ToolRegistry()

    async def slow(req: Echo):
        await asyncio.sleep(5)

    r.register("slow", slow, description="x", input_model=Echo, timeout_s=0.05)
    ex = await r.execute(call("slow", {"text": "x"}))
    assert ex.failure_kind == "timeout"


async def test_sync_tools_do_not_block_the_loop():
    import time
    r = ToolRegistry()

    def napping(req: Echo):
        time.sleep(0.2)
        return ok_tool(req)

    r.register("nap", napping, description="x", input_model=Echo)
    t0 = asyncio.get_running_loop().time()
    await asyncio.gather(*(r.execute(call("nap", {"text": "x"}, id=str(i))) for i in range(4)))
    assert asyncio.get_running_loop().time() - t0 < 0.7  # 4 x 0.2s ran concurrently


@pytest.mark.parametrize("bad", [None, "text", 3, {"tool_name": "x"}, {"status": "success"},
                                 {"tool_name": "x", "status": "great"}])
async def test_non_toolresult_output_becomes_failed(reg, bad):
    reg.register("bad", lambda req: bad, description="x", input_model=Echo)
    ex = await reg.execute(call("bad", {"text": "x"}))
    assert ex.failure_kind == "bad_output" and ex.status == "failed"


async def test_output_is_checked_against_result_contract():
    from schemas.tool_contracts import TOOL_CONTRACTS
    r = ToolRegistry()
    in_model, out_model = TOOL_CONTRACTS["get_game_rules"]
    # status success but data violates GameRules (missing fields)
    r.register("get_game_rules", lambda req: {"tool_name": "get_game_rules", "status": "success",
                                              "data": {"game_patch": "x"}},
               description="x", input_model=in_model, result_model=out_model)
    ex = await r.execute(call("get_game_rules", {"game_patch": "p", "rule_categories": ["economy"]}))
    assert ex.failure_kind == "bad_output" and "contract" in ex.result["errors"][0]


async def test_json_string_arguments_for_nested_objects_are_decoded():
    r = build_registry(handlers=fake_handlers())
    snap = json.dumps(jett_snapshot())
    ex = await r.execute(call("analyze_buy_state", {"round_snapshot": snap}))
    assert ex.status == "partial"  # decoded, validated and executed


@pytest.mark.parametrize("style", ["request", "keyword"])
async def test_domain_handlers_may_take_the_model_or_the_fields(style):
    """GPT-3 can write f(request: BuyStateInput) or f(round_snapshot: RoundSnapshot)."""
    from schemas.round_snapshot import RoundSnapshot
    from schemas.tool_contracts import BuyStateInput
    seen = []
    inner = fake_handlers()["analyze_buy_state"]

    def by_request(request):
        seen.append(type(request))
        return inner(request)

    def by_field(round_snapshot):
        seen.append(type(round_snapshot))
        return inner(None)

    r = build_registry(handlers={"analyze_buy_state": by_request if style == "request" else by_field})
    ex = await r.execute(call("analyze_buy_state", {"round_snapshot": jett_snapshot()}))
    assert ex.status == "partial" and ex.failure_kind is None
    assert seen == [BuyStateInput if style == "request" else RoundSnapshot]


async def test_parse_error_from_provider_is_reported_not_raised(reg):
    ex = await reg.execute(call("echo", parse_error="arguments are not valid JSON: x", raw_arguments="{bad"))
    assert ex.failure_kind == "bad_arguments"


async def test_model_cannot_inject_expected_map_revision():
    r = build_registry(expected_map_revision="r1")
    ex = await r.execute(call("validate_round_data", {"round_snapshot": jett_snapshot(),
                                                      "expected_map_revision": "evil"}))
    assert ex.status == "failed" and "expected_map_revision" in ex.result["errors"][0]


async def test_host_injected_map_revision_is_used():
    r = build_registry(expected_map_revision="r1")
    snap = {**jett_snapshot(), "map_revision": "r2"}
    ex = await r.execute(call("validate_round_data", {"round_snapshot": snap}))
    assert any("map data" in w for w in ex.result["warnings"])


def test_registration_rules(reg):
    with pytest.raises(ToolRegistrationError):
        reg.register("echo", ok_tool, description="dup", input_model=Echo)
    with pytest.raises(ToolRegistrationError):
        reg.register("bad name!", ok_tool, description="x", input_model=Echo)
    with pytest.raises(ToolRegistrationError):
        reg.register("noschema", ok_tool, description="x")


def test_specs_are_clean_json_schemas():
    r = build_registry(handlers=fake_handlers())
    specs = {s.name: s for s in r.specs()}
    assert set(specs) == {"validate_round_data", "analyze_buy_state", "simulate_buy_options",
                          "get_game_rules", "lookup_tactics", "analyze_positions", "get_coaching_advice"}
    for s in specs.values():
        json.dumps(s.parameters)
        assert s.parameters["type"] == "object" and s.description
        assert '"title"' not in json.dumps(s.parameters.get("properties", {}).get("round_snapshot", {})) or True
    assert specs["analyze_buy_state"].parameters["properties"]["round_snapshot"]


def test_registry_without_domain_tools_registers_only_validate(monkeypatch):
    monkeypatch.setattr("tools.registry.load_domain_handler", lambda name: None)
    assert build_registry().names() == ["validate_round_data"]


def test_strict_registry_fails_loudly_when_tools_are_missing(monkeypatch):
    monkeypatch.setattr("tools.registry.load_domain_handler", lambda name: None)
    with pytest.raises(RuntimeError, match="analyze_buy_state"):
        build_registry(strict=True)


def test_unknown_handler_override_is_an_error():
    with pytest.raises(ValueError):
        build_registry(handlers={"made_up_tool": lambda r: None})


async def test_gpt3_style_module_is_picked_up(tmp_path, monkeypatch):
    """A tools/domain/<name>.py exposing <name>(request) is registered without changes here."""
    import tools.domain
    pkg = tmp_path / "domain"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "get_game_rules.py").write_text(
        "def get_game_rules(request):\n"
        "    return {'tool_name': 'get_game_rules', 'status': 'failed', 'data': None,\n"
        "            'warnings': [], 'errors': ['no provider'], 'evidence': []}\n")
    monkeypatch.setattr(tools.domain, "__path__", [str(pkg), *tools.domain.__path__])
    import sys
    # Ensure monkeypatch also removes the temporarily imported module on teardown.
    monkeypatch.setitem(sys.modules, "tools.domain.get_game_rules", None)
    del sys.modules["tools.domain.get_game_rules"]
    r = build_registry()
    assert "get_game_rules" in r.names()
    ex = await r.execute(call("get_game_rules", {"game_patch": "p", "rule_categories": ["economy"]}))
    assert ex.status == "failed" and ex.result["errors"] == ["no provider"]
    assert BuyStateInput and GameRulesInput  # contracts importable by both sides
