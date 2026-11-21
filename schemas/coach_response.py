from typing import Literal, Self

from pydantic import Field, model_validator

from .common import Confidence, Contract, Positive, Text
from .evidence import FieldEvidence


class Finding(Contract):
    title: Text
    severity: Literal["info", "low", "medium", "high"]
    explanation: Text
    evidence_refs: list[Text] = Field(min_length=1)
    confidence: Confidence | None = None


class Recommendation(Contract):
    priority: Positive
    action: Text
    reason: Text
    expected_benefit: Text


class CoachResponse(Contract):
    task_id: Text
    task_status: Literal["success", "partial", "failed"]
    overall_judgement: Text | None = None
    buy_review: Text | None = None
    position_review: Text | None = None
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[FieldEvidence] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=3)
    missing_information: list[Text] = Field(default_factory=list)
    coach_summary: Text

    @model_validator(mode="after")
    def check_refs(self) -> Self:
        names = {e.field_name for e in self.evidence}
        if len(names) != len(self.evidence):
            raise ValueError("report evidence paths must be unique")
        for f in self.findings:
            if not set(f.evidence_refs) <= names:
                raise ValueError("finding refers to missing evidence")
        return self
