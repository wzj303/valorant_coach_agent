import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas import (CoachResponse, MatchReport, TOOL_CONTRACTS, ValidationToolResult)
from schemas.export import build_schema
from schemas.tool_contracts import TeamEconomy
from tools.validate_round_data import validate_round_data


def test_complete_and_roundtrip(load_case):
    r = load_case("complete_round")
    result = validate_round_data(r, strict_mode=True, expected_map_revision=r["map_revision"])
    assert result.status == "success" and result.data.valid
    assert result.data.completeness == 1 and result.data.missing_fields == []
    assert result.data.normalized_snapshot.data_quality.completeness == 1
    assert ValidationToolResult.model_validate_json(result.model_dump_json()) == result
    assert "data_quality" not in r


def test_exact_weights(load_case):
    r = load_case("complete_round")
    p = r["ally_players"][0]
    p["credits_before_buy"] = None
    p["evidence"] = [e for e in p["evidence"] if e["field_name"] != "credits_before_buy"]
    result = validate_round_data(r, expected_map_revision="fixture-r1")
    assert result.data.completeness == 0.99
    assert result.data.missing_fields == ["round_snapshot.ally_players[slot=1].credits_before_buy"]
    assert result.status == "partial" and result.data.valid
    strict = validate_round_data(r, True, expected_map_revision="fixture-r1")
    assert strict.status == "failed" and not strict.data.valid
    assert strict.data.normalized_snapshot.ally_players[0].credits_before_buy is None


def test_reference_map_and_invalid_fixture(load_case):
    r = load_case("invalid_round")
    failed = validate_round_data(r)
    assert failed.status == "failed" and failed.data.normalized_snapshot is None
    assert any("credits_before_buy" in msg for msg in failed.errors)
    r["ally_players"][0]["credits_before_buy"] = 0
    assert "duplicate" in str(validate_round_data(r).errors)
    r["ally_players"].pop()
    result = validate_round_data(r, expected_map_revision="fixture-r1")
    assert result.status == "partial" and "configured map data" in str(result.warnings)


def test_unclassified_money_and_required_keys(load_case):
    r = load_case("jett_example")
    result = validate_round_data(r)
    assert "buy timing unknown" in str(result.warnings)
    assert result.data.completeness == 0.03
    del r["map_revision"]
    assert validate_round_data(r).data.missing_fields == ["round_snapshot.map_revision"]


def test_unverified_reference(load_case):
    result = validate_round_data(load_case("complete_round"))
    assert result.data.completeness == 1 and result.status == "partial"
    assert "revision unavailable" in str(result.warnings)


def test_money_exception(load_case):
    r = load_case("complete_round")
    p = r["ally_players"][0]
    p["credits_remaining"] = p["credits_before_buy"] + 1
    assert validate_round_data(r).status == "failed"
    p["credit_exception_note"] = "User confirmed a refund after the initial balance was captured."
    result = validate_round_data(r, expected_map_revision="fixture-r1")
    assert result.status == "partial" and "refund" in str(result.warnings)


def test_evidence_gap(load_case):
    r = load_case("complete_round")
    r["enemy_players"][0]["evidence"] = []
    result = validate_round_data(r, expected_map_revision="fixture-r1")
    assert result.status == "partial" and "source has not been recorded" in str(result.warnings)


def test_position_with_round(load_case):
    r, p = load_case("complete_round"), load_case("position_snapshot")
    result = validate_round_data(r, position_snapshot=p, expected_map_revision="fixture-r1")
    assert result.data.normalized_snapshot.round_id == result.data.normalized_position_snapshot.round_id
    assert result.data.completeness == 0.875
    for change in ({"round_id": "wrong"}, {"map_revision": "old"}):
        assert validate_round_data(r, position_snapshot=p | change).status == "failed"


@pytest.mark.parametrize("payload", [{}, {"round_snapshot": "bad"}, {"strict_mode": "true"}])
def test_invalid_args(payload):
    result = validate_round_data(**payload)
    assert result.errors == result.data.errors and result.status == "failed"


def test_coach_and_match_reports():
    ev = dict(field_name="r1.ally.1.weapon", status="user_confirmed", source="user_input")
    finding = dict(title="Loadout", severity="info", explanation="Confirmed equipment.",
                   evidence_refs=[ev["field_name"]], confidence=None)
    advice = dict(priority=1, action="Review the shop screenshot.", reason="Balance timing is unknown.",
                  expected_benefit="Distinguish the two credit fields.")
    payload = dict(task_id="t", task_status="partial", evidence=[ev], findings=[finding],
                   recommendations=[advice], missing_information=["credit timing"], coach_summary="One player reviewed.")
    report = CoachResponse(**payload)
    assert CoachResponse.model_validate_json(report.model_dump_json()) == report
    for changes in ({"evidence": []}, {"recommendations": [advice] * 4}):
        with pytest.raises(ValidationError):
            CoachResponse.model_validate(payload | changes)
    match = MatchReport(match_id="m", analyzed_rounds=["r1"], improvement_plan=[advice], final_summary="Reviewed.")
    assert MatchReport.model_validate_json(match.model_dump_json()) == match


def test_contract_index():
    schema = build_schema()
    saved = Path(__file__).resolve().parents[2] / "schemas/json_schema/contracts-1.0.0.json"
    published = json.loads(saved.read_text(encoding="utf-8"))
    # Pydantic patch releases may render metadata differently. Public field sets must agree.
    assert schema["x-tools"].keys() == published["x-tools"].keys() == TOOL_CONTRACTS.keys()
    for name, (request, response) in TOOL_CONTRACTS.items():
        for kind, model in (("input", request), ("output", response)):
            ref = published["x-tools"][name][kind]["$ref"].split("/")[-1]
            assert set(published["$defs"][ref]["properties"]) == set(model.model_fields)
            assert model.model_json_schema()["type"] == "object"


def test_aggregate_unknown_and_false_success():
    base = dict(known_players=0, known_credits=None, time_basis="before_buy", estimated=False, complete=False)
    TeamEconomy(**base)
    for changes in ({"known_credits": 0}, {"known_players": 5}, {"complete": True}):
        with pytest.raises(ValidationError):
            TeamEconomy.model_validate(base | changes)
    result_type = TOOL_CONTRACTS["simulate_buy_options"][1]
    with pytest.raises(ValidationError):
        result_type(tool_name="simulate_buy_options", status="success", data={"plans": []})
