"""Wire contracts only. GPT-3 implements the five domain tools."""
from typing import Literal, Self

from pydantic import Field, model_validator

from .common import Confidence, Contract, Count, MapId, Positive, Side, Slot, Team, Text
from .evidence import FieldEvidence
from .position_snapshot import PositionSnapshot
from .round_snapshot import RoundSnapshot
from .tool_result import ToolResult
from .validation import ValidateRoundInput, ValidationToolResult

Strategy = Literal["full_buy", "half_buy", "eco"]
Category = Literal["economy", "weapons", "armor", "abilities"]


class BuyStateInput(Contract):
    round_snapshot: RoundSnapshot


class BuyConstraints(Contract):
    preserve_next_round_economy: bool = True
    team_coordinated_buy: bool = True


class BuySimulationInput(BuyStateInput):
    strategies: list[Strategy] = Field(min_length=1)
    constraints: BuyConstraints = Field(default_factory=BuyConstraints)


class GameRulesInput(Contract):
    game_patch: Text
    rule_categories: list[Category] = Field(min_length=1)


class TacticsInput(Contract):
    map_id: MapId
    map_revision: Text
    side: Side | None = None
    agents: list[Text] = Field(default_factory=list)
    query: Text
    limit: int = Field(5, ge=1, le=25)


class PositionsInput(Contract):
    position_snapshots: list[PositionSnapshot] = Field(min_length=1)
    map_id: MapId
    map_revision: Text | None


class TeamEconomy(Contract):
    known_credits: Count | None
    known_players: int = Field(ge=0, le=5)
    time_basis: Literal["before_buy", "remaining"]
    estimated: bool
    complete: bool

    @model_validator(mode="after")
    def check_coverage(self) -> Self:
        if self.complete != (self.known_players == 5):
            raise ValueError("complete must reflect five documented player balances")
        if (self.known_players == 0) != (self.known_credits is None):
            raise ValueError("no known balances must be represented by null")
        return self


class BuyStateAnalysis(Contract):
    ally_economy: TeamEconomy
    enemy_economy: TeamEconomy
    weapon_distribution: dict[Team, dict[Text, Count]]
    armor_distribution: dict[Team, dict[Text, Count]]
    ability_completeness: dict[Team, Confidence | None]
    buy_type: dict[Team, Text | None]
    team_buy_coordination: Text | None
    equipment_value: dict[Team, Count | None]
    warnings: list[Text]
    evidence: list[FieldEvidence]

    def incomplete(self) -> bool:
        return not (self.ally_economy.complete and self.enemy_economy.complete)


class PlayerPlan(Contract):
    slot: Slot
    team: Team
    spend: Count | None
    remaining_credits: Count | None
    loadout: list[Text] | None
    ability_status: dict[Text, bool | None] | None


class BuyPlan(Contract):
    plan_id: Text
    strategy: Strategy
    feasible: bool | None
    players: list[PlayerPlan]
    total_spend: Count | None
    remaining_credits: Count | None
    next_round_projection: Text | None
    warnings: list[Text]
    assumptions: list[Text]


class BuyPlanComparison(Contract):
    plans: list[BuyPlan]

    def incomplete(self) -> bool:
        return not self.plans or any(p.feasible is None for p in self.plans)


class GameRules(Contract):
    game_patch: Text
    weapon_prices: dict[Text, Count] | None
    armor_prices: dict[Text, Count] | None
    ability_prices: dict[Text, Count] | None
    round_economy_rules: dict[Text, Count] | None
    loss_bonus_rules: dict[Text, Count] | None
    kill_rewards: dict[Text, Count] | None
    source: Text | None
    verified_at: Text | None
    completeness: Confidence
    warnings: list[Text]

    def incomplete(self) -> bool:
        return self.completeness < 1 or self.source is None or self.verified_at is None


class TacticEntry(Contract):
    rule_id: Text
    title: Text
    content: Text
    applicable_conditions: list[Text]
    source: Text
    map_revision: Text


class TacticQueryResult(Contract):
    entries: list[TacticEntry]
    map_id: MapId
    map_revision: Text
    matched_count: Count
    truncated: bool
    warnings: list[Text]


class PlayerRef(Contract):
    team: Team
    slot: Slot


class PlayerPair(Contract):
    snapshot_id: Text
    first: PlayerRef
    second: PlayerRef


class PlayerDistance(PlayerPair):
    distance: float = Field(ge=0)
    unit: Literal["normalized", "pixel"]


class FrameSummary(Contract):
    snapshot_id: Text
    counts: dict[Text, dict[Team, Count]]


class PositionChange(Contract):
    from_snapshot_id: Text
    to_snapshot_id: Text
    player: PlayerRef
    description: Text


class PositionAnalysis(Contract):
    region_distribution: list[FrameSummary]
    player_distances: list[PlayerDistance]
    isolated_players: dict[Text, list[PlayerRef]]
    support_candidates: list[PlayerPair]
    formation_summary: Text | None
    changes_between_snapshots: list[PositionChange]
    warnings: list[Text]
    evidence: list[FieldEvidence]


# The registry and schema exporter import this single authoritative name map.
TOOL_CONTRACTS = {
    "validate_round_data": (ValidateRoundInput, ValidationToolResult),
    "analyze_buy_state": (BuyStateInput, ToolResult[BuyStateAnalysis]),
    "simulate_buy_options": (BuySimulationInput, ToolResult[BuyPlanComparison]),
    "get_game_rules": (GameRulesInput, ToolResult[GameRules]),
    "lookup_tactics": (TacticsInput, ToolResult[TacticQueryResult]),
    "analyze_positions": (PositionsInput, ToolResult[PositionAnalysis]),
}
