"""Shared V0 scalar types. Use explicit null for unreadable observations."""
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "1.0.0"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _nullable_revision(value):
    if isinstance(value, str) and value.strip().casefold() == "unknown":
        return None
    return value


Revision = Annotated[Text | None, BeforeValidator(_nullable_revision)]
Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]
Slot = Annotated[int, Field(ge=1, le=5)]
Confidence = Annotated[float, Field(ge=0, le=1)]
Team = Literal["ally", "enemy"]
Side = Literal["attack", "defense"]
MapId = Literal["sunset"]
Armor = Literal["none", "light", "heavy", "regen"]
Weapon = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                         pattern=r"^[^/|,;+\n\r]+$")]
Role = Literal["duelist", "initiator", "controller", "sentinel"]
TaskType = Literal["buy_review", "position_review", "match_review"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              revalidate_instances="always")

    def incomplete(self) -> bool:
        """Return True when the payload itself declares insufficient data."""
        return False


class DataQuality(Contract):
    completeness: Confidence | None = None
    missing_fields: list[Text] = Field(default_factory=list)
    warnings: list[Text] = Field(default_factory=list)


def check_roster(players):
    """Slots are local to a team; ally/1 and enemy/1 can coexist."""
    seen = set()
    for player in players:
        key = (player.team, player.slot)
        if key in seen:
            raise ValueError(f"duplicate player slot: {player.team}/{player.slot}")
        seen.add(key)
