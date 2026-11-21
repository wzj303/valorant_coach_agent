from typing import Literal, Self

from pydantic import model_validator

from .common import Confidence, Contract, Text

EvidenceStatus = Literal["observed", "inferred", "user_confirmed", "derived", "unknown"]
Source = Literal["screenshot", "user_input", "tool", "model_inference", "unknown"]


class FieldEvidence(Contract):
    field_name: Text
    status: EvidenceStatus
    source: Source
    confidence: Confidence | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def check_origin(self) -> Self:
        if self.status == "unknown" and self.confidence is not None:
            raise ValueError("unknown evidence cannot have numeric confidence")
        if self.status == "user_confirmed" and self.source != "user_input":
            raise ValueError("confirmation must come from user_input")
        if self.source == "model_inference" and self.status != "inferred":
            raise ValueError("model_inference must be tagged inferred")
        if self.status != "unknown" and self.source == "unknown":
            raise ValueError("known evidence requires a source")
        return self


def index_evidence(entries, allowed):
    """One effective evidence item per field and object."""
    by_name = {}
    for e in entries:
        if e.field_name not in allowed:
            raise ValueError(f"unrecognized evidence path: {e.field_name}")
        if e.field_name in by_name:
            raise ValueError(f"conflicting evidence for {e.field_name}")
        by_name[e.field_name] = e
    return by_name
