from typing import Self

from pydantic import Field, model_validator

from .common import Armor, Contract, Count, Role, Slot, Team, Text, Weapon
from .evidence import FieldEvidence, index_evidence


class PlayerSnapshot(Contract):
    slot: Slot
    team: Team
    agent: Text | None = None
    role: Role | None = None
    credits_before_buy: Count | None = None
    credits_remaining: Count | None = None
    credits_unclassified: Count | None = Field(None, description="Credits with an unreadable or unspecified time basis.")
    credit_exception_note: Text | None = None
    weapon: Weapon | None = None
    secondary_weapon: Weapon | None = None
    armor: Armor | None = None
    abilities: dict[Text, bool | None] | None = None
    evidence: list[FieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_player(self) -> Self:
        allowed = set(type(self).model_fields) - {"evidence"}
        values = self.model_dump(exclude={"evidence"})
        for ability, value in (self.abilities or {}).items():
            key = f"abilities.{ability}"
            allowed.add(key)
            values[key] = value
        indexed = index_evidence(self.evidence, allowed)
        for field, ev in indexed.items():
            if (values[field] is None) != (ev.status == "unknown"):
                raise ValueError(f"{field}: evidence status disagrees with value")
        if self.role is not None and "role" in indexed:
            ev = indexed["role"]
            if ev.status == "derived":
                raise ValueError("mark a role mapped from agent as inferred")
        if (self.credits_before_buy is not None and self.credits_remaining is not None
                and self.credits_remaining > self.credits_before_buy and not self.credit_exception_note):
            raise ValueError("credits_remaining exceeds credits_before_buy without credit_exception_note")
        if self.weapon and self.secondary_weapon and self.weapon.casefold() == self.secondary_weapon.casefold():
            raise ValueError("same weapon listed in both equipment slots")
        return self


def apply_player_observation(player: PlayerSnapshot, value, evidence: FieldEvidence) -> PlayerSnapshot:
    """Merge a scalar observation and its provenance atomically; confirmation wins."""
    field = evidence.field_name
    if field not in type(player).model_fields or field in {"evidence", "slot", "team", "abilities"}:
        raise ValueError("expected a mutable scalar player field")
    previous = next((ev for ev in player.evidence if ev.field_name == field), None)
    if previous and previous.status == "user_confirmed" and evidence.status != "user_confirmed":
        return player
    if evidence.status == "unknown" and getattr(player, field) is not None:
        return player
    payload = player.model_dump()
    payload[field] = value
    payload["evidence"] = [ev for ev in payload["evidence"] if ev["field_name"] != field]
    payload["evidence"].append(evidence.model_dump())
    return PlayerSnapshot.model_validate(payload)
