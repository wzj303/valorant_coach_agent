"""JSON-only inputs and state for sequential, checkpointed match reviews."""
from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from schemas import PositionSnapshot, RoundSnapshot

MAX_MATCH_ROUNDS = 100


class RoundObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_snapshot: RoundSnapshot
    position_snapshot: PositionSnapshot | None = None


class MatchRoundInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    round_index: int = Field(ge=1, le=MAX_MATCH_ROUNDS)
    # Images are stored separately; checkpoints contain references, never duplicate base64.
    images: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    snapshot: dict[str, Any] | None = None
    position_snapshot: dict[str, Any] | None = None
    text: str = Field(default="", max_length=20000)


class MatchState(TypedDict, total=False):
    match_id: str
    mode: str
    text: str
    observations: list[str]
    pending: list[dict[str, Any]]
    cursor: int
    current: dict[str, Any]
    records: list[dict[str, Any]]
    summarize: bool
    report: dict[str, Any] | None
    timeline: list[dict[str, Any]]
    trace: dict[str, Any]
    phase: str
