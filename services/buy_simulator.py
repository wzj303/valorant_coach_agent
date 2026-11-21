"""Deterministic buy plans using only caller-supplied prices.

Full buys spend up to the available credits, half buys spend up to half, and
eco buys spend nothing. Optional armor_cost and ability_cost are included in
non-eco plans. Player budgets are evaluated independently when players exist.
For simple snapshots without strategies or extra costs, options preserves the
legacy weapon comparison. All default plans are exposed in strategy_options.
With players and an explicit team credit cap, purchases use player input order.
"""
from collections.abc import Mapping
from .interfaces import finite_number, require_mapping, result


_STRATEGIES = ("full_buy", "half_buy", "eco")


class BuySimulator:
    def __init__(self, weapon_costs: Mapping, *, version: str = "unknown", source: str = "caller-provided"):
        self.weapon_costs = dict(require_mapping(weapon_costs, "weapon_costs"))
        self.version, self.source = version, source

    def _weapons(self, snapshot, unknown):
        weapons = snapshot.get("weapons", list(self.weapon_costs))
        if not isinstance(weapons, (list, tuple)):
            raise ValueError("weapons must be a list or tuple")
        prices = {}
        if not self.weapon_costs:
            unknown.append("weapon_costs")
        for weapon in weapons:
            if not isinstance(weapon, str) or not weapon.strip():
                raise ValueError("each weapon must be a non-empty string")
            weapon = weapon.strip()
            value = self.weapon_costs.get(weapon)
            if value is None:
                unknown.append(f"weapon:{weapon}")
            else:
                prices[weapon] = finite_number(value, f"weapon_costs[{weapon}]", minimum=0)
        return prices

    def _plan(self, snapshot, strategy, credits, prices, prices_unknown, unknown, prefix="", budget_limit=None):
        budget = credits / 2 if strategy == "half_buy" and credits is not None else credits
        if budget is not None and budget_limit is not None:
            budget = min(budget, budget_limit)
        plan = {"strategy": strategy, "weapon": None, "credits": credits, "budget": budget}
        if strategy == "eco":
            plan.update(cost=0.0, affordable=credits is not None, remaining_credits=credits)
            if credits is None:
                plan["affordable"] = None
            return plan
        extra = 0.0
        missing_cost = False
        for field in ("armor_cost", "ability_cost"):
            value = snapshot.get(field, 0)
            if value is None:
                unknown.append(prefix + field)
                missing_cost = True
            else:
                extra += finite_number(value, prefix + field, minimum=0)
        if credits is None or prices_unknown or missing_cost:
            plan.update(cost=None, affordable=None, remaining_credits=None)
            return plan
        if extra > budget:
            plan.update(cost=0.0, affordable=False, remaining_credits=credits, required_cost=extra)
            return plan
        affordable = [(cost, weapon) for weapon, cost in prices.items() if cost + extra <= budget]
        # Highest-priced affordable weapon, with a stable alphabetical tie break.
        if affordable:
            cost, weapon = sorted(affordable, key=lambda item: (-item[0], item[1]))[0]
            plan["weapon"] = weapon
        else:
            cost = 0.0
        total = cost + extra
        plan.update(cost=total, affordable=bool(affordable), remaining_credits=credits - total)
        return plan

    def simulate_buy_options(self, snapshot: Mapping) -> dict:
        snapshot = require_mapping(snapshot)
        strategies = snapshot.get("strategies", _STRATEGIES)
        if not isinstance(strategies, (list, tuple)):
            raise ValueError("strategies must be a list or tuple")
        if any(strategy not in _STRATEGIES for strategy in strategies):
            raise ValueError("strategies must contain full_buy, half_buy, or eco")
        strategies = list(dict.fromkeys(strategies))
        unknown = []
        prices = self._weapons(snapshot, unknown) if any(strategy != "eco" for strategy in strategies) else {}
        price_unknown = bool(unknown)
        players = snapshot.get("players")
        if players is not None and not isinstance(players, (list, tuple)):
            raise ValueError("players must be a list or tuple")
        budgets = []
        if players is not None:
            if not players:
                unknown.append("players")
            for index, player in enumerate(players):
                player = require_mapping(player, f"players[{index}]")
                value = player.get("credits")
                if value is None:
                    unknown.append("players[].credits")
                credits = finite_number(value, "players[].credits", minimum=0) if value is not None else None
                player_snapshot = dict(snapshot)
                player_snapshot.update(player)
                budgets.append((player_snapshot, credits))
            credits = sum(value for _, value in budgets) if budgets and all(value is not None for _, value in budgets) else None
        else:
            value = snapshot.get("credits")
            if value is None:
                unknown.append("credits")
            credits = finite_number(value, "credits", minimum=0) if value is not None else None
            budgets.append((snapshot, credits))
        team_credits = snapshot.get("credits")
        if team_credits is not None:
            team_credits = finite_number(team_credits, "credits", minimum=0)
            credits = team_credits
        plans = []
        for strategy in strategies:
            purchases = []
            remaining_budget = credits / 2 if strategy == "half_buy" and credits is not None else credits
            for index, (player, player_credits) in enumerate(budgets):
                player_prices, missing_prices = prices, price_unknown
                if strategy != "eco" and players is not None and "weapons" in players[index]:
                    local_unknown = []
                    player_prices = self._weapons(player, local_unknown)
                    unknown.extend(local_unknown)
                    missing_prices = bool(local_unknown)
                purchase = self._plan(player, strategy, player_credits, player_prices, missing_prices, unknown,
                                      "players[]." if players is not None else "", remaining_budget)
                purchases.append(purchase)
                if remaining_budget is not None and purchase["cost"] is not None:
                    remaining_budget -= purchase["cost"]
            if players is None:
                plans.extend(purchases)
                continue
            known_cost = bool(purchases) and all(item["cost"] is not None for item in purchases)
            cost = sum(item["cost"] for item in purchases) if known_cost else None
            budget = credits / 2 if strategy == "half_buy" and credits is not None else credits
            affordable = None
            if cost is not None and budget is not None and all(item["affordable"] is not None for item in purchases):
                affordable = all(item["affordable"] for item in purchases) and cost <= budget
            plans.append({"strategy": strategy, "weapon": None, "credits": credits, "budget": budget,
                          "players": purchases, "cost": cost, "affordable": affordable,
                          "remaining_credits": credits - cost if credits is not None and cost is not None else None})
        options = plans
        if "strategies" not in snapshot and players is None and "armor_cost" not in snapshot and "ability_cost" not in snapshot:
            options = []
            if credits is not None:
                for weapon, cost in prices.items():
                    options.append({"strategy": "full_buy", "weapon": weapon, "cost": cost,
                                    "affordable": cost <= credits, "remaining_credits": credits - cost})
                options.sort(key=lambda item: (not item["affordable"], item["cost"], item["weapon"]))
        unknown = list(dict.fromkeys(unknown))
        return result({"options": options, "strategy_options": plans, "credits": credits},
                      source=self.source, version=self.version, status="unknown" if unknown else "ok", unknown=unknown)


def simulate_buy_options(snapshot: Mapping, weapon_costs: Mapping, **kwargs) -> dict:
    return BuySimulator(weapon_costs, **kwargs).simulate_buy_options(snapshot)
