from typing import Generic, Literal, Self, TypeVar

from pydantic import Field, model_validator

from .common import Contract, Text
from .evidence import FieldEvidence

T = TypeVar("T", bound=Contract)


class ToolResult(Contract, Generic[T]):
    tool_name: Text
    status: Literal["success", "partial", "failed"]
    data: T | None
    warnings: list[Text] = Field(default_factory=list)
    errors: list[Text] = Field(default_factory=list)
    evidence: list[FieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_outcome(self) -> Self:
        if self.status == "failed":
            if not self.errors:
                raise ValueError("failed tool must explain its errors")
        elif self.data is None or self.errors:
            raise ValueError("usable tool output requires data and no errors")
        if self.status == "success" and self.data is not None and self.data.incomplete():
            raise ValueError("incomplete data requires a partial result with warnings")
        if self.status == "partial" and not self.warnings:
            raise ValueError("partial tool must explain missing or uncertain data")
        return self
