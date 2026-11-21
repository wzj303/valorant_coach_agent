"""Mapping-based adapters for tactical services and local prototype datasets."""
from collections.abc import Mapping
from pathlib import Path
import json

from services.buy_simulator import BuySimulator
from services.economy_engine import EconomyEngine
from services.game_rules_provider import GameRulesProvider
from services.interfaces import SERVICE_VERSION, non_empty_string, require_mapping, result
from services.knowledge_service import KnowledgeService
from services.position_analyzer import PositionAnalyzer

_ROOT = Path(__file__).resolve().parents[2]


def _failed(error):
    response = result({}, source="domain-tools", status="failed")
    response["error"] = str(error)
    return response


def _load(relative_path):
    with (_ROOT / relative_path).open(encoding="utf-8") as file:
        return json.load(file)


def _dataset_metadata(dataset, source):
    return {
        "source": source,
        "service_version": SERVICE_VERSION,
        "dataset_id": dataset.get("dataset_id"),
        "schema_version": dataset.get("schema_version", "unknown"),
        "patch": dataset.get("patch"),
        "source_status": dataset.get("source_status", "unknown"),
    }


def analyze_buy_state(inputs: Mapping) -> dict:
    try:
        inputs = require_mapping(inputs)
        snapshot = require_mapping(inputs.get("round_data"), "round_data")
        return EconomyEngine(inputs.get("rules")).analyze_buy_state(snapshot)
    except (ValueError, TypeError) as error:
        return _failed(error)


def simulate_buy_options(inputs: Mapping) -> dict:
    try:
        inputs = require_mapping(inputs)
        snapshot = dict(require_mapping(inputs.get("round_data"), "round_data"))
        if "strategies" in inputs:
            snapshot["strategies"] = inputs["strategies"]
        prices = inputs.get("weapon_costs", {})
        return BuySimulator(prices).simulate_buy_options(snapshot)
    except (ValueError, TypeError) as error:
        return _failed(error)


def get_game_rules(inputs: Mapping) -> dict:
    try:
        inputs = require_mapping(inputs)
        category = inputs.get("category")
        if category is not None:
            category = non_empty_string(category, "category")
        rules = inputs.get("rules")
        source = "local-json" if rules is None else "caller-provided"
        if rules is None:
            rules = _load("knowledge/game_rules/economy.json")
        rules = require_mapping(rules, "rules")
        if "credit_rules" in rules and "weapons" in rules:
            selected = {}
            for group in ("credit_rules", "weapons", "armor", "abilities"):
                entries = rules.get(group, [])
                if not isinstance(entries, list):
                    raise ValueError(f"{group} must be a list")
                for entry in entries:
                    entry = require_mapping(entry, "rule entry")
                    key = non_empty_string(entry.get("id"), "rule id")
                    if category is None or category in (group, key, entry.get("category")):
                        selected[key] = entry
            unknown = []
            if category is not None and not selected:
                unknown.append(f"rule:{category}")
            if rules.get("source_status") != "verified":
                unknown.append("game-rules:source-unverified")
            response = result(
                {"rules": selected, "patch": rules.get("patch"), "source_status": rules.get("source_status")},
                source=source, status="unknown" if unknown else "ok", unknown=unknown,
            )
            response["metadata"] = _dataset_metadata(rules, source)
            response["metadata"]["rule_version"] = inputs.get("rule_version", rules.get("schema_version", "unknown"))
            return response
        provider = GameRulesProvider(rules, version=inputs.get("rule_version", "unknown"))
        return provider.get_rule({"key": category}) if category is not None else provider.list_rules()
    except (ValueError, TypeError, OSError) as error:
        return _failed(error)


def lookup_tactics(inputs: Mapping) -> dict:
    try:
        inputs = require_mapping(inputs)
        map_id = non_empty_string(inputs.get("map_id", "sunset"), "map_id")
        category = inputs.get("category")
        if category is not None:
            category = non_empty_string(category, "category")
        query = inputs.get("query")
        if query is not None:
            query = non_empty_string(query, "query")
        limit = inputs.get("limit")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            raise ValueError("limit must be a non-negative integer")
        knowledge = inputs.get("knowledge")
        if knowledge is None:
            if not all(character.isalnum() or character in "_-" for character in map_id):
                raise ValueError("map_id must contain only letters, digits, underscores or hyphens")
            try:
                dataset = require_mapping(_load(f"knowledge/maps/{map_id.lower()}/tactics.json"), "tactics dataset")
            except FileNotFoundError:
                return result({"map_id": map_id, "matches": []}, source="local-json", status="unknown", unknown=[f"tactics:{map_id}"])
            entries = dataset.get("entries", [])
            if not isinstance(entries, list):
                raise ValueError("tactics entries must be a list")
            entries = [require_mapping(entry, "tactics entry") for entry in entries]
            term = (query or "").casefold()
            matches = [entry for entry in entries if (not term or term in str(entry).casefold()) and (category is None or entry.get("category") == category)]
            unknown = [] if matches else [f"tactics-search:{query or category or map_id}"]
            if dataset.get("source_status") != "verified":
                unknown.append("tactics:source-unverified")
            response = result({"map_id": map_id, "matches": matches if limit is None else matches[:limit]}, source="local-json", status="unknown" if unknown else "ok", unknown=unknown)
            response["metadata"] = _dataset_metadata(dataset, "local-json")
            return response
        knowledge = require_mapping(knowledge, "knowledge")
        if category is not None:
            knowledge = {key: value for key, value in knowledge.items() if isinstance(value, Mapping) and value.get("category") == category}
        if query is None:
            matches = [{"key": key, "value": value} for key, value in knowledge.items()]
            response = result(
                {"matches": matches}, source="caller-provided", version="unknown",
                status="ok" if matches else "unknown",
                unknown=[] if matches else [f"tactics-search:{category or map_id}"],
            )
        else:
            response = KnowledgeService(knowledge).search({"query": query})
        if limit is not None:
            response["data"]["matches"] = response["data"]["matches"][:limit]
        return response
    except (ValueError, TypeError, OSError) as error:
        return _failed(error)


def analyze_positions(inputs: Mapping) -> dict:
    try:
        inputs = require_mapping(inputs)
        snapshot = inputs.get("positions", inputs.get("round_data"))
        if isinstance(snapshot, list):
            snapshot = {"map": inputs.get("map", inputs.get("map_id", "sunset")), "positions": snapshot}
        snapshot = dict(require_mapping(snapshot, "positions or round_data"))
        map_name = non_empty_string(snapshot.get("map", inputs.get("map_id", "sunset")), "map")
        snapshot["map"] = map_name
        maps = inputs.get("maps")
        dataset = None
        if maps is None:
            if not all(character.isalnum() or character in "_-" for character in map_name):
                raise ValueError("map must contain only letters, digits, underscores or hyphens")
            try:
                dataset = require_mapping(_load(f"knowledge/maps/{map_name.lower()}/map.json"), "map dataset")
            except FileNotFoundError:
                maps = {}
            else:
                maps = {map_name: dataset}
        maps = dict(require_mapping(maps, "maps"))
        for name, map_data in maps.items():
            map_data = dict(require_mapping(map_data, "map data"))
            regions = map_data.get("regions", {})
            if isinstance(regions, list):
                lookup = {}
                for region in regions:
                    region = require_mapping(region, "region")
                    lookup[non_empty_string(region.get("id"), "region id")] = region
                map_data["regions"] = lookup
            else:
                require_mapping(regions, "regions")
            maps[name] = map_data
        source = "local-json" if inputs.get("maps") is None else "caller-provided"
        analyzer = PositionAnalyzer(maps, source=source)
        positions = snapshot.get("positions")
        if positions is None:
            response = analyzer.analyze_position(snapshot)
        else:
            if not isinstance(positions, list):
                raise ValueError("positions must be a list")
            analyzed = []
            distribution = {}
            unknown = []
            for position in positions:
                if isinstance(position, str):
                    position = {"region": position}
                position = dict(require_mapping(position, "position"))
                position["map"] = map_name
                item = analyzer.analyze_position(position)
                analyzed.append({**position, **item["data"]})
                region = item["data"]["region"]
                distribution[region] = distribution.get(region, 0) + 1
                unknown.extend(item["unknown"])
            unknown.append("support:topology-unknown")
            response = result(
                {"map": map_name, "positions": analyzed, "support_distribution": distribution},
                source=source, status="unknown", unknown=list(dict.fromkeys(unknown)),
            )
        if dataset is None and isinstance(maps.get(map_name), Mapping) and "source_status" in maps[map_name]:
            dataset = maps[map_name]
        if dataset is not None:
            response["metadata"] = _dataset_metadata(dataset, source)
            if dataset.get("source_status") != "verified":
                response["status"] = "unknown"
                response["unknown"].append("positions:source-unverified")
        return response
    except (ValueError, TypeError, OSError) as error:
        return _failed(error)
