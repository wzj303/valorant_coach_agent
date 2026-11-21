import unittest

from services.economy_engine import EconomyEngine


class EconomyEdgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = EconomyEngine()

    def assert_unknown_totals(self, snapshot):
        result = self.engine.analyze_buy_state(snapshot)
        data = result["data"]
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(data["credits"])
        self.assertIsNone(data["planned_cost"])
        self.assertIsNone(data["affordable"])
        self.assertIsNone(data["remaining_credits"])
        return result

    def test_empty_players_do_not_become_zero_or_affordable(self):
        result = self.assert_unknown_totals({"players": []})
        self.assertIn("players", result["unknown"])

    def test_non_mapping_player_makes_team_totals_unknown(self):
        result = self.assert_unknown_totals(
            {
                "players": [
                    {"credits": 1_000, "weapon_cost": 500, "utility_cost": 100},
                    "malformed",
                ]
            }
        )
        self.assertIn("players[]", result["unknown"])

    def test_missing_player_field_makes_only_affected_total_unknown(self):
        result = self.engine.analyze_buy_state(
            {
                "players": [
                    {"credits": 1_000, "weapon_cost": 500, "utility_cost": 100},
                    {"credits": 900, "weapon_cost": None, "utility_cost": 100},
                ]
            }
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["data"]["credits"], 1_900)
        self.assertIsNone(result["data"]["planned_cost"])
        self.assertIsNone(result["data"]["affordable"])
        self.assertIsNone(result["data"]["remaining_credits"])
        self.assertIn("players[].weapon_cost", result["unknown"])

    def test_missing_credits_does_not_default_to_zero(self):
        result = self.engine.analyze_buy_state(
            {"players": [{"weapon_cost": 500, "utility_cost": 100}]}
        )
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["data"]["credits"])
        self.assertIsNone(result["data"]["affordable"])
        self.assertIn("players[].credits", result["unknown"])

    def test_invalid_numeric_values_are_rejected(self):
        invalid_snapshots = [
            {"credits": "1000", "weapon_cost": 500, "utility_cost": 100},
            {"credits": -1, "weapon_cost": 500, "utility_cost": 100},
            {"players": [{"credits": 1_000, "weapon_cost": float("nan"), "utility_cost": 100}]},
            {"players": [{"credits": True, "weapon_cost": 500, "utility_cost": 100}]},
        ]
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(ValueError):
                    self.engine.analyze_buy_state(snapshot)

    def test_non_list_players_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_buy_state({"players": {"credits": 1_000}})


if __name__ == "__main__":
    unittest.main()
