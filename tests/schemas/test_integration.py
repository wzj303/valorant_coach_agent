import json

import pytest
from pydantic import ValidationError

from examples.agent2_integration import agent1_validate, agent3_buy_input, agent3_encode, registry_entry
from schemas import TOOL_CONTRACTS, RoundSnapshot
from tools.validate_round_data import validate_round_data


def test_unknown_revision_marker(load_case):
    r = RoundSnapshot.model_validate(load_case("jett_example") | {"map_revision": " UNKNOWN "})
    assert r.map_revision is None
    assert "round_snapshot.map_revision" in validate_round_data(r).data.missing_fields


def test_registry_trusted_context(load_case):
    r = load_case("complete_round")
    assert agent1_validate({"round_snapshot": r, "expected_map_revision": "fake"}).status == "failed"
    validated = agent1_validate({"round_snapshot": r}, provider_revision="fixture-r1")
    assert validated.status == "success"
    assert agent3_buy_input(validated).round_snapshot == validated.data.normalized_snapshot
    raw = json.loads(agent3_encode("get_game_rules", "failed", None, errors=["unconfigured"]))
    assert raw["status"] == "failed" and raw["errors"] == ["unconfigured"]


def test_domain_requests(load_case):
    r = load_case("complete_round")
    p = load_case("position_snapshot")
    common_map = {"map_id": "sunset", "map_revision": "fixture-r1"}
    requests = {
        "validate_round_data": {"round_snapshot": r, "position_snapshot": p},
        "analyze_buy_state": {"round_snapshot": r},
        "simulate_buy_options": {"round_snapshot": r, "strategies": ["full_buy", "eco"]},
        "get_game_rules": {"game_patch": "fixture-patch", "rule_categories": ["economy"]},
        "lookup_tactics": {**common_map, "query": "Mid"},
        "analyze_positions": {**common_map, "position_snapshots": [p]},
    }
    for name, payload in requests.items():
        model, _ = TOOL_CONTRACTS[name]
        obj = model.model_validate(payload)
        assert model.model_validate_json(obj.model_dump_json()) == obj
        assert registry_entry(name)["parameters"]["additionalProperties"] is False


def test_partial_requires_explanation():
    _, response = TOOL_CONTRACTS["simulate_buy_options"]
    for status in ("success", "partial", "failed"):
        with pytest.raises(ValidationError):
            response(tool_name="simulate_buy_options", status=status, data={"plans": []})
    out = response(tool_name="simulate_buy_options", status="partial",
                   data={"plans": []}, warnings=["Missing pre-buy balances."])
    assert response.model_validate_json(out.model_dump_json()) == out
