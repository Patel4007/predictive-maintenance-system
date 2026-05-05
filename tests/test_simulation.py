from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from predictive_maintenance_system.config import Settings
from predictive_maintenance_system.simulation import PredictiveMaintenanceEngine


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            update_interval_seconds=0.05,
            initial_warmup_steps=24,
            history_limit=48,
            asset_chart_points=12,
        )
        self.engine = PredictiveMaintenanceEngine(self.settings)

    def test_engine_builds_dashboard_snapshot(self) -> None:
        snapshot = self.engine.dashboard_snapshot()
        self.assertEqual(snapshot.metrics.asset_count, 6)
        self.assertEqual(len(snapshot.assets), 6)
        self.assertTrue(snapshot.maintenance_schedule)
        self.assertGreaterEqual(snapshot.assets[0].probability_7d, 0.0)

    def test_scenario_injection_is_reflected_in_snapshot(self) -> None:
        result = self.engine.inject_scenario("lubrication-loss", duration_hours=12)
        self.assertEqual(result["status"], "ok")
        self.engine.step()
        snapshot = self.engine.dashboard_snapshot()
        self.assertTrue(any(insight.source == "lubrication-loss" for insight in snapshot.insights))

    def test_service_asset_moves_machine_into_maintenance(self) -> None:
        result = self.engine.service_asset("AIR-01")
        self.assertEqual(result["status"], "ok")
        snapshot = self.engine.dashboard_snapshot()
        asset = next(item for item in snapshot.assets if item.asset_id == "AIR-01")
        self.assertEqual(asset.state, "maintenance")


if __name__ == "__main__":
    unittest.main()
