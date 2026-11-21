"""Lookup game rules from an explicitly supplied mapping."""
from collections.abc import Mapping
from .interfaces import non_empty_string, require_mapping, result


class GameRulesProvider:
    def __init__(self, rules: Mapping, *, version: str = "unknown", source: str = "caller-provided"):
        self.rules = dict(require_mapping(rules, "rules"))
        self.version = non_empty_string(version, "version")
        self.source = non_empty_string(source, "source")

    def get_rule(self, query: Mapping) -> dict:
        query = require_mapping(query)
        key = non_empty_string(query.get("key"), "key")
        if key not in self.rules:
            return result({"key": key, "value": None}, status="unknown", unknown=[f"rule:{key}"], source=self.source, version=self.version)
        return result({"key": key, "value": self.rules[key]}, source=self.source, version=self.version)

    def list_rules(self, query: Mapping | None = None) -> dict:
        if query is not None:
            require_mapping(query)
        return result({"rules": dict(self.rules)}, source=self.source, version=self.version)


def get_rule(query: Mapping, rules: Mapping, *, version: str = "unknown", source: str = "caller-provided") -> dict:
    return GameRulesProvider(rules, version=version, source=source).get_rule(query)
