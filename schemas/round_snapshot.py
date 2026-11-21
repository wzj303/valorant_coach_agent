from typing import Literal, Self

from pydantic import Field, model_validator

from .common import Contract, Count, DataQuality, MapId, Positive, Revision, Side, Text, check_roster
from .evidence import FieldEvidence, index_evidence
from .player_snapshot import PlayerSnapshot


class Score(Contract):
    ally: Count | None = None
    enemy: Count | None = None


class RoundSnapshot(Contract):
    match_id: Text
    round_id: Text
    round_index: Positive
    map_id: MapId
    map_revision: Revision
    game_patch: Text | None = None
    side: Side | None = None
    score: Score | None = None
    round_phase: Literal["buy", "live", "post_round"] | None = None
    ally_players: list[PlayerSnapshot] | None = Field(None, max_length=5)
    enemy_players: list[PlayerSnapshot] | None = Field(None, max_length=5)
    ally_loss_streak: Count | None = None
    enemy_loss_streak: Count | None = None
    data_quality: DataQuality | None = None
    source_images: list[Text] | None = None
    user_notes: list[Text] | None = None
    evidence: list[FieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_sides(self) -> Self:
        for team, players in (("ally", self.ally_players), ("enemy", self.enemy_players)):
            for player in players or ():
                if player.team != team:
                    raise ValueError(f"{team}_players contains a player assigned to {player.team}")
            check_roster(players or ())
        values = self.model_dump(exclude={"evidence"})
        for team in ("ally", "enemy"):
            values[f"score.{team}"] = getattr(self.score, team, None)
        for field, ev in index_evidence(self.evidence, values).items():
            if (values[field] is None) != (ev.status == "unknown"):
                raise ValueError(f"{field}: evidence status disagrees with value")
        return self
