"""Regression coverage for the domain adapters' local and caller-provided paths."""
import unittest
from unittest.mock import patch

from tools.domain import (
    analyze_buy_state,
    analyze_positions,
    get_game_rules,
    lookup_tactics,
    simulate_buy_options,
)


class ToolsReviewTests(unittest.TestCase):
    def test_failed_results_always_include_metadata(self):
        for tool in (analyze_buy_state, simulate_buy_options, get_game_rules, lookup_tactics, analyze_positions):
            with self.subTest(tool=tool.__name__):
                response = tool(None)
                self.assertEqual(response["status"], "failed")
                self.assertIn("source", response["metadata"])
                self.assertIn("service_version", response["metadata"])

    def test_default_local_rules_filter_and_version(self):
        response = get_game_rules({"category": "rifle"})
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(set(response["data"]["rules"]), {"bulldog", "guardian", "phantom", "vandal"})
        self.assertEqual(response["metadata"]["source"], "local-json")
        self.assertEqual(response["metadata"]["schema_version"], "0.1.0")
        self.assertEqual(response["metadata"]["rule_version"], "0.1.0")
        self.assertIn("game-rules:source-unverified", response["unknown"])
        self.assertIsNone(response["data"]["rules"]["vandal"]["price"])

    def test_custom_rules_missing_category(self):
        response = get_game_rules({"rules": {"cap": 100}, "category": "missing"})
        self.assertEqual(response["status"], "unknown")
        self.assertIn("rule:missing", response["unknown"])
        dataset = {"credit_rules": [], "weapons": [], "source_status": "verified"}
        response = get_game_rules({"rules": dataset, "category": "missing"})
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["metadata"]["source"], "caller-provided")
        self.assertIn("rule:missing", response["unknown"])

    def test_default_local_tactics_and_filters(self):
        response = lookup_tactics({})
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["metadata"]["source"], "local-json")
        self.assertEqual(len(response["data"]["matches"]), 1)
        self.assertEqual(len(lookup_tactics({"category": "general", "query": "SUNSET"})["data"]["matches"]), 1)
        missing = lookup_tactics({"category": "missing"})
        self.assertEqual(missing["status"], "unknown")
        self.assertEqual(missing["data"]["matches"], [])
        self.assertIn("tactics-search:missing", missing["unknown"])
        self.assertEqual(lookup_tactics({"limit": 0})["data"]["matches"], [])

    def test_custom_tactics_filters_and_limit(self):
        knowledge = {
            "hold": {"category": "defense", "text": "hold mid"},
            "push": {"category": "attack", "text": "push mid"},
        }
        response = lookup_tactics({"knowledge": knowledge, "query": "mid", "category": "defense", "limit": 1})
        self.assertEqual(response["status"], "ok")
        self.assertEqual([match["key"] for match in response["data"]["matches"]], ["hold"])
        filtered = lookup_tactics({"knowledge": knowledge, "category": "defense"})
        self.assertEqual([match["key"] for match in filtered["data"]["matches"]], ["hold"])
        missing = lookup_tactics({"knowledge": knowledge, "query": "mid", "category": "absent"})
        self.assertEqual(missing["status"], "unknown")
        self.assertEqual(missing["data"]["matches"], [])
        self.assertTrue(missing["unknown"])

    def test_limits_and_categories_validated_for_both_sources(self):
        for knowledge in (None, {"mid": "hold mid"}):
            for limit in (-1, True, 1.5, "1", [], {}):
                with self.subTest(knowledge=knowledge, limit=limit):
                    response = lookup_tactics({"knowledge": knowledge, "limit": limit})
                    self.assertEqual(response["status"], "failed")
                    self.assertIn("metadata", response)
            for category in ("", 4, [], {}):
                with self.subTest(knowledge=knowledge, category=category):
                    self.assertEqual(lookup_tactics({"knowledge": knowledge, "category": category})["status"], "failed")

    def test_missing_local_datasets_return_unknown(self):
        self.assertEqual(lookup_tactics({"map_id": "missing"})["status"], "unknown")
        response = analyze_positions({"positions": {"map": "missing", "region": "mid"}})
        self.assertEqual(response["status"], "unknown")
        self.assertIn("position:missing:mid", response["unknown"])

    def test_local_read_errors_are_failed_results(self):
        with patch("tools.domain.tools._load", side_effect=OSError("unreadable")):
            for tool, inputs in (
                (lookup_tactics, {}),
                (get_game_rules, {}),
                (analyze_positions, {"positions": {"region": "mid"}}),
            ):
                with self.subTest(tool=tool.__name__):
                    response = tool(inputs)
                    self.assertEqual(response["status"], "failed")
                    self.assertIn("metadata", response)

    def test_default_local_position_lookup(self):
        response = analyze_positions({"positions": {"region": "mid"}})
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["data"]["map"], "sunset")
        self.assertEqual(response["data"]["position"]["id"], "mid")
        self.assertIsNone(response["data"]["position"]["coordinates"])
        self.assertIn("positions:source-unverified", response["unknown"])
        self.assertEqual(response["metadata"]["dataset_id"], "valorant.sunset.prototype")

    def test_position_array_distribution_preserves_unknowns(self):
        response = analyze_positions({"positions": ["mid", {"player_id": "two", "region": "mid"}, {"region": "missing"}]})
        self.assertEqual(response["status"], "unknown")
        self.assertEqual(response["data"]["support_distribution"], {"mid": 2, "missing": 1})
        self.assertEqual(len(response["data"]["positions"]), 3)
        self.assertIn("position:sunset:missing", response["unknown"])
        self.assertIn("support:topology-unknown", response["unknown"])
        self.assertIsNone(response["data"]["positions"][2]["position"])

    def test_custom_map_region_lists_and_legacy_mapping(self):
        for regions in ([{"id": "mid", "label": "Mid"}], {"mid": {"id": "mid", "label": "Mid"}}):
            with self.subTest(regions=regions):
                response = analyze_positions({"maps": {"test": {"regions": regions}}, "positions": {"map": "test", "region": "mid"}})
                self.assertEqual(response["status"], "ok")
                self.assertEqual(response["data"]["position"]["label"], "Mid")
                self.assertEqual(response["metadata"]["source"], "caller-provided")


if __name__ == "__main__":
    unittest.main()
