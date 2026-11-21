"""Deterministic service checks using synthetic inputs, runnable with unittest."""
import unittest

from services.buy_simulator import BuySimulator
from services.economy_engine import EconomyEngine
from services.game_rules_provider import GameRulesProvider
from services.knowledge_service import KnowledgeService
from services.position_analyzer import PositionAnalyzer
from tools.domain import analyze_buy_state, simulate_buy_options, get_game_rules, lookup_tactics, analyze_positions


class ServiceTests(unittest.TestCase):
    def test_rules_known_and_unknown(self):
        provider = GameRulesProvider({"cap": 100}, version="test")
        self.assertEqual(provider.get_rule({"key": "cap"})["data"]["value"], 100)
        self.assertEqual(provider.get_rule({"key": "missing"})["status"], "unknown")

    def test_economy_affordability(self):
        result = EconomyEngine().analyze_buy_state({"credits": 500, "weapon_cost": 300, "utility_cost": 100})
        self.assertTrue(result["data"]["affordable"])
        self.assertEqual(result["data"]["remaining_credits"], 100)
        self.assertFalse(EconomyEngine().analyze_buy_state({"credits": 10, "weapon_cost": 20})["data"]["affordable"])

    def test_missing_values_remain_unknown_in_economy(self):
        result = EconomyEngine().analyze_buy_state({"credits": 500})
        self.assertIsNone(result["data"]["planned_cost"])
        self.assertIsNone(result["data"]["affordable"])
        self.assertIn("weapon_cost", result["unknown"])

        team = EconomyEngine().analyze_buy_state({"players": [{"credits": 500, "weapon_cost": 100}, {"credits": None, "weapon_cost": 0}]})
        self.assertIsNone(team["data"]["credits"])
        self.assertIn("players[].credits", team["unknown"])

    def test_simulator_sorting_and_unknown(self):
        result = BuySimulator({"rifle": 300, "pistol": 100}).simulate_buy_options({"credits": 150, "weapons": ["rifle", "missing", "pistol"]})
        self.assertEqual([item["weapon"] for item in result["data"]["options"]], ["pistol", "rifle"])
        self.assertEqual(result["unknown"], ["weapon:missing"])
        self.assertFalse(result["data"]["options"][1]["affordable"])

    def test_tactics_search(self):
        service = KnowledgeService({"sunset": "hold mid", "other": "rotate"})
        self.assertEqual(service.search({"query": "MID"})["data"]["matches"][0]["key"], "sunset")
        self.assertEqual(service.search({"query": "absent"})["status"], "unknown")

    def test_position_known_and_unknown(self):
        service = PositionAnalyzer({"test": {"regions": {"mid": {"label": "Mid"}}}})
        self.assertEqual(service.analyze_position({"map": "test", "region": "mid"})["data"]["position"]["label"], "Mid")
        self.assertEqual(service.analyze_position({"map": "test", "region": "unknown"})["status"], "unknown")

    def test_tool_validation(self):
        for tool in (analyze_buy_state, simulate_buy_options, get_game_rules, lookup_tactics, analyze_positions):
            with self.subTest(tool=tool.__name__):
                self.assertEqual(tool(None)["status"], "failed")

    def test_tool_delegation(self):
        self.assertEqual(analyze_buy_state({"round_data": {"credits": 5}})["data"]["credits"], 5)
        result = simulate_buy_options({"round_data": {"credits": 10, "weapons": ["x"]}, "weapon_costs": {"x": 5}})
        self.assertTrue(result["data"]["options"][0]["affordable"])
        self.assertEqual(get_game_rules({"rules": {"x": 3}, "category": "x"})["data"]["value"], 3)
        self.assertEqual(lookup_tactics({"query": "mid", "knowledge": {"x": "mid"}})["status"], "ok")
        self.assertEqual(analyze_positions({"positions": {"map": "m", "region": "r"}, "maps": {"m": {"regions": {"r": 1}}}})["data"]["position"], 1)


if __name__ == "__main__":
    unittest.main()
