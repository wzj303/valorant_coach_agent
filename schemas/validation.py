from typing import Self

from pydantic import Field, model_validator

from .common import Confidence, Contract, Text
from .position_snapshot import PositionSnapshot
from .round_snapshot import RoundSnapshot
from .tool_result import ToolResult


class ValidateRoundInput(Contract):
    round_snapshot: RoundSnapshot | None = None
    position_snapshot: PositionSnapshot | None = None
    strict_mode: bool = False

    @model_validator(mode="after")
    def check_pair(self) -> Self:
        r, p = self.round_snapshot, self.position_snapshot
        if r is None and p is None:
            raise ValueError("supply a round_snapshot or position_snapshot")
        if r and p and r.round_id != p.round_id:
            raise ValueError("position_snapshot.round_id differs from round_snapshot.round_id")
        if r and p and r.map_revision and p.map_revision and r.map_revision != p.map_revision:
            raise ValueError("paired snapshots have inconsistent map revisions")
        return self


class ValidationResult(Contract):
    valid: bool
    completeness: Confidence | None
    missing_fields: list[Text] = Field(default_factory=list)
    warnings: list[Text] = Field(default_factory=list)
    errors: list[Text] = Field(default_factory=list)
    normalized_snapshot: RoundSnapshot | PositionSnapshot | None = None
    normalized_position_snapshot: PositionSnapshot | None = None

    def incomplete(self) -> bool:
        return not self.valid or self.completeness != 1 or bool(self.warnings or self.missing_fields)


ValidationToolResult = ToolResult[ValidationResult]
