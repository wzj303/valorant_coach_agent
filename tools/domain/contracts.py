"""Expose the mapping-based tactical engine through the agent's public contracts."""
from collections import Counter
from itertools import combinations
from math import dist

from schemas.tool_contracts import (
    TOOL_CONTRACTS, BuySimulationInput, BuyStateInput, GameRulesInput, PositionsInput, TacticsInput,
)
from . import tools as engine


def _result(name, data, warnings=(), evidence=()):
    warnings = list(dict.fromkeys(warnings))
    return TOOL_CONTRACTS[name][1](
        tool_name=name, status="partial" if warnings else "success", data=data,
        warnings=warnings, evidence=list(evidence),
    )


def _check(raw):
    if raw["status"] == "failed":
        raise ValueError(raw.get("error", "tactical service failed"))
    return raw


def analyze_buy_state(request: BuyStateInput):
    snapshot = request.round_snapshot
    teams = {"ally": snapshot.ally_players or [], "enemy": snapshot.enemy_players or []}
    basis = "before_buy"
    if not any(p.credits_before_buy is not None for team in teams.values() for p in team):
        if any(p.credits_remaining is not None for team in teams.values() for p in team):
            basis = "remaining"
    field = f"credits_{basis}"
    balances, weapons, armor, abilities = {}, {}, {}, {}
    warnings = ["尚无已验证的装备估值和购买类型规则。"]
    evidence = []
    for team, players in teams.items():
        known = [p for p in players if getattr(p, field) is not None]
        amounts = [{"credits": getattr(p, field)} for p in known]
        total = _check(engine.analyze_buy_state({"round_data": {"players": amounts}}))["data"]["credits"]
        estimated = False
        for p in players:
            for ev in p.evidence:
                if ev.field_name == field and ev.status == "inferred":
                    estimated = True
                evidence.append(ev.model_copy(update={"field_name": f"{team}.{p.slot}.{ev.field_name}"}))
        balances[team] = dict(known_credits=int(total) if total is not None else None,
                              known_players=len(known), time_basis=basis, estimated=estimated,
                              complete=len(known) == 5)
        weapons[team] = dict(Counter(p.weapon for p in players if p.weapon is not None))
        armor[team] = dict(Counter(p.armor for p in players if p.armor is not None))
        # Each of the five slots contributes its fraction of reported ability states.
        coverage = [sum(v is not None for v in p.abilities.values()) / len(p.abilities)
                    for p in players if p.abilities]
        abilities[team] = sum(coverage) / 5 if players else None
        if len(known) < 5:
            label = "己方" if team == "ally" else "敌方"
            timing = "购买前" if basis == "before_buy" else "购买后"
            warnings.append(f"{label}仅有 {len(known)}/5 名玩家的{timing}金币记录。")
        if any(p.credits_unclassified is not None for p in players):
            warnings.append(f"{team} 有金币时点未确认，已从余额统计中排除。")
    unknown = dict.fromkeys(teams, None)
    data = dict(ally_economy=balances["ally"], enemy_economy=balances["enemy"],
                weapon_distribution=weapons, armor_distribution=armor, ability_completeness=abilities,
                buy_type=unknown, equipment_value=unknown, team_buy_coordination=None,
                warnings=warnings, evidence=evidence)
    return _result("analyze_buy_state", data, warnings, evidence)


def get_game_rules(request: GameRulesInput):
    groups = dict(economy="credit_rules", weapons="weapons", armor="armor", abilities="abilities")
    data = dict(game_patch=request.game_patch, weapon_prices=None, armor_prices=None, ability_prices=None,
                round_economy_rules=None, loss_bonus_rules=None, kill_rewards=None, source=None,
                verified_at=None, completeness=0.0)
    warnings = []
    categories = list(dict.fromkeys(request.rule_categories))
    complete = 0
    for category in categories:
        raw = _check(engine.get_game_rules({"category": groups[category]}))
        meta = raw["metadata"]
        data["source"] = meta.get("dataset_id") or meta.get("source")
        if (meta.get("patch") != request.game_patch or meta.get("source_status") != "verified"
                or not meta.get("verified_at")):
            warnings.append(f"本地规则尚未核验所请求的游戏版本 {request.game_patch}。")
            continue
        data["verified_at"] = meta["verified_at"]
        records = raw["data"]["rules"]
        key = "value" if category == "economy" else "price"
        values = {name: row[key] for name, row in records.items()
                  if row.get("source_status") == "verified" and type(row.get(key)) is int and row[key] >= 0}
        if category == "economy":
            data["loss_bonus_rules"] = {k: v for k, v in values.items() if k.startswith("loss_reward")} or None
            data["kill_rewards"] = {k: v for k, v in values.items() if k == "kill_reward"} or None
            data["round_economy_rules"] = {k: v for k, v in values.items()
                                           if not k.startswith("loss_reward") and k != "kill_reward"} or None
        else:
            column = {"weapons": "weapon_prices", "armor": "armor_prices", "abilities": "ability_prices"}[category]
            data[column] = values or None
        if records and len(values) == len(records):
            complete += 1
        else:
            warnings.append(f"{category} 缺少已核验的规则条目。")
    data["completeness"] = complete / len(categories)
    data["warnings"] = warnings
    return _result("get_game_rules", data, warnings)


def simulate_buy_options(request: BuySimulationInput):
    snapshot, constraints = request.round_snapshot, request.constraints
    roster = snapshot.ally_players or []
    prices, warnings = {}, []
    if any(s != "eco" for s in request.strategies):
        if snapshot.game_patch:
            rules = get_game_rules(GameRulesInput(game_patch=snapshot.game_patch, rule_categories=["weapons"]))
            if rules.status == "success":
                prices = rules.data.weapon_prices or {}
            warnings.extend(rules.warnings)
        else:
            warnings.append("缺少游戏版本，无法核验武器价格。")
    budgets = [{"credits": p.credits_before_buy} for p in roster]
    raw = _check(engine.simulate_buy_options({"round_data": {"players": budgets}, "weapon_costs": prices,
                                              "strategies": request.strategies}))
    if len(roster) != 5:
        warnings.append("己方阵容不完整，整队合计与可行性未知。")
    if any(p.credits_before_buy is None for p in roster):
        warnings.append("部分购买前余额未知。")
    plans = []
    for option in raw["data"]["strategy_options"]:
        strategy = option["strategy"]
        notes = []
        if strategy != "eco":
            notes.append("购买模拟仅比较武器预算，护甲、技能和下回合收入尚未纳入。")
            if constraints.preserve_next_round_economy:
                notes.append("缺少下回合经济预测，无法确认保留经济约束。")
            if constraints.team_coordinated_buy:
                notes.append("同策略按人计算，团队协同效果尚未核验。")
        players = []
        for p, purchase in zip(roster, option["players"], strict=True):
            acquired = purchase.get("weapon")
            gear = [w for w in (p.weapon, p.secondary_weapon) if w and w != "none"]
            if acquired:
                gear = [acquired] + ([p.secondary_weapon] if p.secondary_weapon and p.secondary_weapon != "none" else [])
            players.append(dict(team=p.team, slot=p.slot, spend=_integer(purchase["cost"]),
                                remaining_credits=_integer(purchase["remaining_credits"]),
                                loadout=gear or None, ability_status=p.abilities))
        full = len(roster) == 5
        feasible = option["affordable"] if full else None
        if notes and feasible is True:
            feasible = None
        plans.append(dict(plan_id=f"{snapshot.round_id}-{strategy}", strategy=strategy, feasible=feasible,
                          players=players, total_spend=_integer(option["cost"]) if full else None,
                          remaining_credits=_integer(option["remaining_credits"]) if full else None,
                          next_round_projection=None, warnings=list(warnings) + notes,
                          assumptions=["按各玩家购买前余额计算；eco 表示本轮新增支出为零。"]))
    all_warnings = [w for plan in plans for w in plan["warnings"]]
    return _result("simulate_buy_options", {"plans": plans}, all_warnings)


def _integer(value):
    return int(value) if value is not None else None


def lookup_tactics(request: TacticsInput):
    raw = _check(engine.lookup_tactics({"map_id": request.map_id, "query": request.query}))
    meta = raw["metadata"]
    warnings = []
    entries = []
    if meta.get("source_status") != "verified" or meta.get("map_revision") != request.map_revision:
        warnings.append("本地战术资料缺少与请求地图修订相符的已验证条目。")
    else:
        for row in raw["data"]["matches"]:
            if row.get("source_status") != "verified":
                continue
            if row.get("side") and row["side"] != request.side:
                continue
            if row.get("agents") and not set(row["agents"]).issubset(request.agents):
                continue
            entries.append(dict(rule_id=row["id"], title=row["title"], content=row["text"],
                                applicable_conditions=row.get("applicable_conditions", []),
                                source=meta["dataset_id"], map_revision=request.map_revision))
    data = dict(map_id=request.map_id, map_revision=request.map_revision, entries=entries[:request.limit],
                matched_count=len(entries), truncated=len(entries) > request.limit, warnings=warnings)
    return _result("lookup_tactics", data, warnings)


def analyze_positions(request: PositionsInput):
    frames = request.position_snapshots
    if len({f.snapshot_id for f in frames}) != len(frames) or len({f.round_id for f in frames}) != 1:
        raise ValueError("position_snapshots must have unique frame IDs and belong to one round")
    revisions = {r for r in [request.map_revision, *(f.map_revision for f in frames)] if r is not None}
    if len(revisions) > 1 or any(f.map_id != request.map_id for f in frames):
        raise ValueError("inconsistent map or revision across frames")
    distribution, distances, evidence = [], [], []
    warnings = ["地图拓扑与视线尚未核验，无法判定孤立位置或支援关系。"]
    for frame in frames:
        players = frame.players or []
        regions = [dict(team=p.team, region=p.region) for p in players if p.region]
        raw = _check(engine.analyze_positions({"map_id": frame.map_id, "positions": regions}))
        meta = raw["metadata"]
        if (not frame.map_revision or meta.get("map_revision") != frame.map_revision
                or meta.get("source_status") != "verified"):
            warnings.append("本地地图修订尚未与该帧核验，仅报告截图内的分布及坐标间距。")
        counts = {}
        for item in raw["data"]["positions"]:
            by_team = counts.setdefault(item["region"], {})
            team = item["team"]
            by_team[team] = by_team.get(team, 0) + 1
        distribution.append(dict(snapshot_id=frame.snapshot_id, counts=counts))
        reliable = [p for p in players if p.x is not None and p.y is not None
                    and p.status in ("observed", "user_confirmed")]
        for a, b in combinations(reliable, 2):
            distances.append(dict(snapshot_id=frame.snapshot_id, first=dict(team=a.team, slot=a.slot),
                                  second=dict(team=b.team, slot=b.slot), unit=frame.coordinate_system,
                                  distance=dist((a.x, a.y), (b.x, b.y))))
        for ev in frame.evidence:
            evidence.append(ev.model_copy(update={"field_name": f"{frame.snapshot_id}.{ev.field_name}"}))
        if not players or len(regions) < len(players):
            warnings.append(f"{frame.snapshot_id} 的区域分布只包含已知区域。")
    data = dict(region_distribution=distribution, player_distances=distances, isolated_players={},
                support_candidates=[], formation_summary=None, changes_between_snapshots=[],
                warnings=warnings, evidence=evidence)
    if len(frames) > 1:
        warnings.append("当前仅按帧统计，未计算移动变化。")
    return _result("analyze_positions", data, warnings, evidence)


HANDLERS = {handler.__name__: handler for handler in (
    analyze_buy_state, simulate_buy_options, get_game_rules, lookup_tactics, analyze_positions,
)}
