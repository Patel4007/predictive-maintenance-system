from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from predictive_maintenance_system.analytics import MaintenancePredictor
from predictive_maintenance_system.scheduler import MaintenanceScheduler, ScheduleCandidate


@dataclass
class FakeSpec:
    base_vibration_mm_s: float = 2.0
    base_temperature_c: float = 40.0
    base_power_kw: float = 50.0
    base_pressure_bar: float = 5.0
    criticality: int = 5
    asset_type: str = "compressor"


@dataclass
class FakePoint:
    vibration_mm_s: float
    temperature_c: float
    power_kw: float
    pressure_bar: float
    load_pct: float
    throughput_pct: float
    lubricant_pct: float
    anomaly_index: float


class AnalyticsTests(unittest.TestCase):
    def test_predictor_flags_degraded_history_as_high_risk(self) -> None:
        predictor = MaintenancePredictor()
        spec = FakeSpec()
        history = [
            FakePoint(
                vibration_mm_s=3.9,
                temperature_c=59.0,
                power_kw=61.0,
                pressure_bar=4.2,
                load_pct=85.0,
                throughput_pct=76.0,
                lubricant_pct=28.0,
                anomaly_index=0.78,
            )
            for _ in range(24)
        ]

        prediction = predictor.predict(spec, history, state="degraded", hours_since_maintenance=280.0)
        self.assertGreater(prediction.probability_7d, 0.6)
        self.assertLess(prediction.health_score, 70.0)
        self.assertIn(prediction.risk_level, {"high", "critical"})

    def test_scheduler_prioritizes_failed_asset_first(self) -> None:
        predictor = MaintenancePredictor()
        spec = FakeSpec()
        high_risk_history = [
            FakePoint(3.8, 58.0, 60.0, 4.0, 84.0, 74.0, 24.0, 0.82)
            for _ in range(24)
        ]
        low_risk_history = [
            FakePoint(2.1, 41.0, 50.5, 5.1, 64.0, 98.0, 92.0, 0.06)
            for _ in range(24)
        ]
        high_risk_prediction = predictor.predict(spec, high_risk_history, state="failed", hours_since_maintenance=320.0)
        low_risk_prediction = predictor.predict(spec, low_risk_history, state="running", hours_since_maintenance=48.0)
        scheduler = MaintenanceScheduler(crew_capacity=2, slot_hours=8)

        schedule = scheduler.build_schedule(
            datetime.now(timezone.utc),
            [
                ScheduleCandidate("AIR-01", "Air Compressor", "running", 3, 3.0, 2100.0, low_risk_prediction),
                ScheduleCandidate("CNC-02", "CNC Spindle", "failed", 5, 6.0, 6800.0, high_risk_prediction),
            ],
        )

        self.assertTrue(schedule)
        self.assertEqual(schedule[0].asset_id, "CNC-02")
        self.assertEqual(schedule[0].priority_label, "Immediate")


if __name__ == "__main__":
    unittest.main()
