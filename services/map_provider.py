"""Map and site lookups over caller-provided map data."""
from collections.abc import Mapping
from .interfaces import non_empty_string, require_mapping, result


class MapProvider:
    def __init__(self, maps: Mapping, *, version: str = "unknown", source: str = "caller-provided"):
        self.maps = dict(require_mapping(maps, "maps"))
        self.version, self.source = version, source

    def get_map(self, query: Mapping) -> dict:
        query = require_mapping(query)
        name = non_empty_string(query.get("map"), "map")
        if name not in self.maps:
            return result({"map": name, "value": None}, source=self.source, version=self.version, status="unknown", unknown=[f"map:{name}"])
        return result({"map": name, "value": self.maps[name]}, source=self.source, version=self.version)

    def get_site(self, query: Mapping) -> dict:
        query = require_mapping(query)
        name = non_empty_string(query.get("map"), "map")
        site = non_empty_string(query.get("site"), "site")
        entry = self.maps.get(name)
        sites = entry.get("sites", {}) if isinstance(entry, Mapping) else {}
        if not isinstance(sites, Mapping) or site not in sites:
            return result({"map": name, "site": site, "value": None}, source=self.source, version=self.version, status="unknown", unknown=[f"site:{name}:{site}"])
        return result({"map": name, "site": site, "value": sites[site]}, source=self.source, version=self.version)

    def list_maps(self, query: Mapping | None = None) -> dict:
        if query is not None:
            require_mapping(query)
        return result({"maps": list(self.maps)}, source=self.source, version=self.version)
