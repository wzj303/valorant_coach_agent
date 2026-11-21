"""Run from the extracted root: python -m examples.agent2_integration"""
import json
from pathlib import Path

from schemas import CoachResponse, TOOL_CONTRACTS
from schemas.tool_contracts import BuyStateInput
from tools.validate_round_data import validate_round_data


def registry_entry(name):
    request_type, _ = TOOL_CONTRACTS[name]
    return {"name": name, "description": f"VALORANT Coach V0: {name}",
            "parameters": request_type.model_json_schema()}


def agent1_validate(raw_arguments, provider_revision=None):
    # Only the process hosting the tool may supply the map-provider revision.
    model, _ = TOOL_CONTRACTS["validate_round_data"]
    allowed = set(model.model_fields)
    if not isinstance(raw_arguments, dict) or raw_arguments.keys() - allowed:
        from schemas.validation import ValidationResult, ValidationToolResult
        errors = ["expected a JSON object with registered tool arguments"]
        return ValidationToolResult(tool_name="validate_round_data", status="failed", errors=errors,
                                    data=ValidationResult(valid=False, completeness=None, errors=errors))
    return validate_round_data(**raw_arguments, expected_map_revision=provider_revision)


def agent3_buy_input(validation):
    if validation.status == "failed":
        raise ValueError(validation.errors)
    return BuyStateInput(round_snapshot=validation.data.normalized_snapshot)


def agent3_encode(tool_name, status, data, *, warnings=(), errors=()):
    _, result_type = TOOL_CONTRACTS[tool_name]
    return result_type(tool_name=tool_name, status=status, data=data,
                       warnings=list(warnings), errors=list(errors)).model_dump_json()


def main():
    root = Path(__file__).resolve().parents[1]
    single = json.loads((root / "tests/schemas/fixtures/jett_example.json").read_text(encoding="utf-8"))
    print(registry_entry("analyze_buy_state")["name"])
    validated = agent1_validate({"round_snapshot": single})
    buy_input = agent3_buy_input(validated)
    jett = buy_input.round_snapshot.ally_players[0]
    print(validated.status, jett.agent, jett.credits_unclassified, jett.credits_before_buy)
    # GPT-3 can encode its own provider failure through the agreed output schema.
    print(agent3_encode("get_game_rules", "failed", None, errors=["No fixture provider configured."]))
    report = CoachResponse(task_id="sample", task_status="partial",
                           missing_information=["credits_before_buy", "credits_remaining"],
                           coach_summary="已确认 Jett 使用 Vandal 和轻甲。4199 金币的时点待确认。")
    print(report.model_dump_json())


if __name__ == "__main__":
    main()
