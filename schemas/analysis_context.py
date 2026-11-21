from typing import Self

from pydantic import Field, model_validator

from .coach_response import CoachResponse
from .common import Contract, DataQuality, TaskType, Text
from .position_snapshot import PositionSnapshot
from .round_snapshot import RoundSnapshot


class AnalysisContext(Contract):
    task_id: Text
    task_type: TaskType
    match_id: Text
    round_snapshots: list[RoundSnapshot] = Field(default_factory=list)
    position_snapshots: list[PositionSnapshot] = Field(default_factory=list)
    user_observations: list[Text] = Field(default_factory=list)
    previous_analysis: list[CoachResponse] = Field(default_factory=list)
    data_quality: DataQuality | None = None

    @model_validator(mode="after")
    def check_links(self) -> Self:
        rounds = {r.round_id: r for r in self.round_snapshots}
        if len(rounds) != len(self.round_snapshots) or any(r.match_id != self.match_id for r in rounds.values()):
            raise ValueError("duplicate round_id or foreign match_id")
        if len({p.snapshot_id for p in self.position_snapshots}) != len(self.position_snapshots):
            raise ValueError("duplicate snapshot_id")
        for p in self.position_snapshots:
            if p.round_id not in rounds:
                raise ValueError(f"unknown round_id: {p.round_id}")
            r = rounds[p.round_id]
            if p.map_revision and r.map_revision and p.map_revision != r.map_revision:
                raise ValueError("position and round map revisions disagree")
        return self
