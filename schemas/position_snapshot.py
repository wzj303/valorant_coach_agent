from typing import Literal, Self

from pydantic import Field, model_validator

from .common import Confidence, Contract, Count, MapId, Revision, Slot, Team, Text, check_roster
from .evidence import EvidenceStatus, FieldEvidence, index_evidence

CoordinateSystem = Literal["normalized", "pixel", "region_only"]


class PlayerPosition(Contract):
    slot: Slot
    team: Team
    agent: Text | None = None
    x: float | None = Field(None, ge=0)
    y: float | None = Field(None, ge=0)
    region: Text | None = None
    status: EvidenceStatus
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def check_unknown(self) -> Self:
        known = any(v is not None for v in (self.x, self.y, self.region))
        if (self.status == "unknown") == known:
            raise ValueError("unknown position must have no location; known position needs a region or axis")
        if self.status == "unknown" and self.confidence is not None:
            raise ValueError("unknown position cannot have confidence")
        return self


class PositionSnapshot(Contract):
    snapshot_id: Text
    round_id: Text
    map_id: MapId
    map_revision: Revision
    timestamp_ms: Count | None = None
    coordinate_system: CoordinateSystem | None = None
    players: list[PlayerPosition] | None = Field(None, max_length=10)
    evidence: list[FieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_positions(self) -> Self:
        players = self.players or []
        check_roster(players)
        values = self.model_dump(exclude={"evidence"})
        for p in players:
            for k, v in p.model_dump().items():
                values[f"players.{p.team}.{p.slot}.{k}"] = v
            for axis in (p.x, p.y):
                if axis is None:
                    continue
                if self.coordinate_system in (None, "region_only"):
                    raise ValueError("numeric coordinates require a coordinate system")
                if self.coordinate_system == "normalized" and axis > 1:
                    raise ValueError("normalized coordinates must be in [0, 1]")
        for field, ev in index_evidence(self.evidence, values).items():
            if (values[field] is None) != (ev.status == "unknown"):
                raise ValueError(f"{field}: evidence status disagrees with value")
            if field.startswith("players.") and field.rsplit(".", 1)[1] in {"x", "y", "region"}:
                prefix = field.rsplit(".", 1)[0]
                if values[field] is not None and ev.status != values[f"{prefix}.status"]:
                    raise ValueError(f"{field}: position and evidence status disagree")
        return self
