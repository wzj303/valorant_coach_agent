"""Region-level position analysis with explicit unknown handling."""
from collections.abc import Mapping
from .interfaces import non_empty_string, require_mapping, result


class PositionAnalyzer:
    def __init__(self, maps: Mapping, *, version: str = "unknown", source: str = "caller-provided"):
        self.maps = dict(require_mapping(maps, "maps"))
        self.version, self.source = version, source

    def analyze_position(self, snapshot: Mapping) -> dict:
        snapshot = require_mapping(snapshot)
        map_name = non_empty_string(snapshot.get("map"), "map")
        region = non_empty_string(snapshot.get("region"), "region")
        map_data = self.maps.get(map_name)
        regions = map_data.get("regions", {}) if isinstance(map_data, Mapping) else {}
        if not isinstance(regions, Mapping) or region not in regions:
            return result({"map": map_name, "region": region, "position": None}, source=self.source, version=self.version, status="unknown", unknown=[f"position:{map_name}:{region}"])
        return result({"map": map_name, "region": region, "position": regions[region]}, source=self.source, version=self.version)
