"""Run from the project root: python -m schemas.export"""
import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from . import (SCHEMA_VERSION, TOOL_CONTRACTS, AnalysisContext, CoachResponse, FieldEvidence,
               MatchReport, PlayerSnapshot, PositionSnapshot, RoundSnapshot, ValidationResult)


def build_schema():
    models = [AnalysisContext, CoachResponse, FieldEvidence, MatchReport, PlayerSnapshot,
              PositionSnapshot, RoundSnapshot, ValidationResult]
    for input_model, result_model in TOOL_CONTRACTS.values():
        models.extend([input_model, result_model])
    refs, schema = models_json_schema([(m, "validation") for m in models],
                                      title=f"VALORANT AI Coach {SCHEMA_VERSION}")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-contract-version"] = SCHEMA_VERSION
    schema["x-models"] = {m.__name__: refs[m, "validation"] for m in models}
    schema["x-tools"] = {tool: {"input": refs[i, "validation"], "output": refs[o, "validation"]}
                         for tool, (i, o) in TOOL_CONTRACTS.items()}
    return schema


if __name__ == "__main__":
    output = Path(__file__).with_name("json_schema") / "contracts-1.0.0.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
