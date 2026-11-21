"""Simple deterministic knowledge lookup and substring search."""
from collections.abc import Mapping
from .interfaces import non_empty_string, require_mapping, result


class KnowledgeService:
    def __init__(self, knowledge: Mapping, *, version: str = "unknown", source: str = "caller-provided"):
        self.knowledge = dict(require_mapping(knowledge, "knowledge"))
        self.version, self.source = version, source

    def get_entry(self, query: Mapping) -> dict:
        query = require_mapping(query)
        key = non_empty_string(query.get("key"), "key")
        if key not in self.knowledge:
            return result({"key": key, "value": None}, source=self.source, version=self.version, status="unknown", unknown=[f"knowledge:{key}"])
        return result({"key": key, "value": self.knowledge[key]}, source=self.source, version=self.version)

    def search(self, query: Mapping) -> dict:
        query = require_mapping(query)
        term = non_empty_string(query.get("query"), "query").casefold()
        matches = []
        for key, value in self.knowledge.items():
            if term in str(key).casefold() or term in str(value).casefold():
                matches.append({"key": key, "value": value})
        return result({"matches": matches}, source=self.source, version=self.version, status="ok" if matches else "unknown", unknown=[] if matches else [f"knowledge-search:{term}"])
