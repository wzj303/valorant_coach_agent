"""Deterministic economy state analysis."""
from collections.abc import Mapping

from .interfaces import finite_number, require_mapping, result


class EconomyEngine:
    def __init__(
        self,
        rules: Mapping | None = None,
        *,
        version: str = "unknown",
        source: str = "caller-provided",
    ):
        self.rules = dict(rules or {})
        self.version = version
        self.source = source

    def analyze_buy_state(self, snapshot: Mapping) -> dict:
        snapshot = require_mapping(snapshot)
        players = snapshot.get("players")

        if players is None:
            credits = self._optional_number(snapshot, "credits", "credits")
            weapon_cost = self._optional_number(snapshot, "weapon_cost", "weapon_cost")
            utility_cost = self._optional_number(snapshot, "utility_cost", "utility_cost")
            unknown = [
                name
                for name, value in (
                    ("credits", credits),
                    ("weapon_cost", weapon_cost),
                    ("utility_cost", utility_cost),
                )
                if value is None
            ]
        else:
            if not isinstance(players, (list, tuple)):
                raise ValueError("players must be a list or tuple")
            credits, weapon_cost, utility_cost, unknown = self._aggregate_players(players)

        planned_cost = (
            weapon_cost + utility_cost
            if weapon_cost is not None and utility_cost is not None
            else None
        )
        affordable = (
            credits >= planned_cost
            if credits is not None and planned_cost is not None
            else None
        )
        remaining_credits = (
            credits - planned_cost if affordable is not None else None
        )

        data = {
            "credits": credits,
            "planned_cost": planned_cost,
            "affordable": affordable,
            "remaining_credits": remaining_credits,
        }
        return result(
            data,
            source=self.source,
            version=self.version,
            status="unknown" if unknown else "ok",
            unknown=unknown,
        )

    @staticmethod
    def _optional_number(snapshot: Mapping, field: str, label: str) -> float | None:
        value = snapshot.get(field)
        if value is None:
            return None
        return finite_number(value, label, minimum=0)

    @classmethod
    def _aggregate_players(
        cls, players: list | tuple
    ) -> tuple[float | None, float | None, float | None, list[str]]:
        if not players:
            return None, None, None, ["players"]

        malformed_players = any(not isinstance(player, Mapping) for player in players)
        unknown: list[str] = []
        if malformed_players:
            unknown.append("players[]")

        valid_players = [player for player in players if isinstance(player, Mapping)]
        credits = cls._sum_player_field(valid_players, "credits", unknown)
        weapon_cost = cls._sum_player_field(valid_players, "weapon_cost", unknown)
        utility_cost = cls._sum_player_field(valid_players, "utility_cost", unknown)

        # A malformed entry may represent a player whose values are unavailable,
        # so no complete team total can be reported for any aggregate.
        if malformed_players:
            credits = None
            weapon_cost = None
            utility_cost = None

        return credits, weapon_cost, utility_cost, unknown

    @staticmethod
    def _sum_player_field(
        players: list[Mapping], field: str, unknown: list[str]
    ) -> float | None:
        values = [player.get(field) for player in players]
        if any(value is None for value in values):
            marker = f"players[].{field}"
            if marker not in unknown:
                unknown.append(marker)
            return None

        return sum(
            finite_number(value, f"players[].{field}", minimum=0)
            for value in values
        )
