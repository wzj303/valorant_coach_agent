"""Strategy regression tests with synthetic, caller-provided prices."""
import unittest
from collections import UserDict

from services.buy_simulator import BuySimulator
from tools.domain import simulate_buy_options


class BuyStrategyTests(unittest.TestCase):
    def setUp(self):
        self.simulator = BuySimulator({"rifle": 300, "pistol": 100, "alternate": 300}, version="synthetic")

    def test_default_strategies_and_legacy_comparison(self):
        response = self.simulator.simulate_buy_options(UserDict(credits=500))
        plans = response["data"]["strategy_options"]
        self.assertEqual([plan["strategy"] for plan in plans], ["full_buy", "half_buy", "eco"])
        self.assertEqual([plan["weapon"] for plan in plans], ["alternate", "pistol", None])
        self.assertEqual([plan["cost"] for plan in plans], [300, 100, 0])
        self.assertEqual(response["metadata"], {"source": "caller-provided", "service_version": "synthetic"})
        self.assertTrue(all("strategy" in option for option in response["data"]["options"]))

    def test_costs_fit_strategy_budget(self):
        response = self.simulator.simulate_buy_options({"credits": 400, "strategies": ["full_buy", "half_buy", "eco"],
                                                        "armor_cost": 50, "ability_cost": 75})
        full, half, eco = response["data"]["options"]
        self.assertEqual((full["weapon"], full["cost"], full["remaining_credits"]), ("pistol", 225, 175))
        self.assertEqual((half["cost"], half["affordable"]), (125, False))
        self.assertEqual(eco["cost"], 0)
        for plan in (full, half, eco):
            self.assertLessEqual(plan["cost"], plan["budget"])
            self.assertGreaterEqual(plan["remaining_credits"], 0)

    def test_insufficient_optional_budget_does_not_overspend(self):
        response = self.simulator.simulate_buy_options({"credits": 20, "armor_cost": 30, "strategies": ["full_buy"]})
        plan = response["data"]["options"][0]
        self.assertEqual((plan["cost"], plan["remaining_credits"], plan["affordable"]), (0, 20, False))

    def test_players_have_independent_budgets(self):
        response = self.simulator.simulate_buy_options({"players": [{"credits": 50}, {"credits": 550}],
                                                        "strategies": ["full_buy"]})
        plan = response["data"]["options"][0]
        self.assertFalse(plan["affordable"])
        self.assertEqual(plan["cost"], 300)
        self.assertEqual([player["cost"] for player in plan["players"]], [0, 300])
        self.assertEqual(response["data"]["credits"], 600)

    def test_explicit_team_cap_limits_total_spending(self):
        response = self.simulator.simulate_buy_options({"credits": 400, "players": [{"credits": 500}, {"credits": 500}],
                                                        "strategies": ["full_buy"]})
        plan = response["data"]["options"][0]
        self.assertEqual(plan["cost"], 400)
        self.assertEqual([player["cost"] for player in plan["players"]], [300, 100])
        self.assertEqual(plan["remaining_credits"], 0)
        self.assertTrue(plan["affordable"])

    def test_unknown_credits_are_not_discarded(self):
        response = self.simulator.simulate_buy_options({"players": [{"credits": 100}, {}], "strategies": ["half_buy"]})
        self.assertEqual(response["status"], "unknown")
        self.assertIsNone(response["data"]["credits"])
        self.assertIsNone(response["data"]["options"][0]["affordable"])
        self.assertIn("players[].credits", response["unknown"])

    def test_unavailable_prices_and_explicit_unknown_optional_cost(self):
        for prices, extra, unknown in [({}, {}, "weapon_costs"), ({"rifle": None}, {}, "weapon:rifle"),
                                       ({"rifle": 300}, {"armor_cost": None}, "armor_cost")]:
            with self.subTest(prices=prices, extra=extra):
                response = BuySimulator(prices).simulate_buy_options(dict(credits=500, strategies=["full_buy"], **extra))
                self.assertEqual(response["status"], "unknown")
                self.assertIn(unknown, response["unknown"])
                self.assertIsNone(response["data"]["options"][0]["cost"])
                self.assertIsNone(response["data"]["options"][0]["affordable"])

    def test_eco_requires_no_prices(self):
        response = BuySimulator({}).simulate_buy_options({"credits": 500, "strategies": ["eco"]})
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["options"][0]["remaining_credits"], 500)

    def test_team_budget_and_wrapper_forwarding(self):
        response = simulate_buy_options({"round_data": UserDict(credits=500, ability_cost=25),
                                         "weapon_costs": {"rifle": 300, "pistol": 100},
                                         "strategies": ["half_buy", "eco"]})
        self.assertEqual(response["status"], "ok")
        half, eco = response["data"]["options"]
        self.assertEqual((half["strategy"], half["cost"], half["budget"]), ("half_buy", 125, 250))
        self.assertEqual(eco["strategy"], "eco")

    def test_invalid_values(self):
        for snapshot in ({"credits": True}, {"credits": float("nan")}, {"credits": -1},
                         {"credits": 10, "armor_cost": -1}, {"players": [None]},
                         {"credits": 10, "strategies": ["unsupported"]}, {"credits": 10, "strategies": "eco"}):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(ValueError):
                    self.simulator.simulate_buy_options(snapshot)


if __name__ == "__main__":
    unittest.main()
