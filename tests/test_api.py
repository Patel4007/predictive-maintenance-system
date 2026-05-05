from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from predictive_maintenance_system.api import create_app
from predictive_maintenance_system.config import Settings


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            update_interval_seconds=0.05,
            initial_warmup_steps=18,
            history_limit=40,
            asset_chart_points=10,
        )
        self.client_context = TestClient(create_app(self.settings))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    def test_dashboard_endpoint_returns_assets_and_schedule(self) -> None:
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metrics"]["asset_count"], 6)
        self.assertEqual(len(payload["assets"]), 6)
        self.assertIn("maintenance_schedule", payload)

    def test_index_versions_static_assets_and_disables_caching(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("/static/app.js?v=", response.text)

    def test_execute_maintenance_endpoint_changes_state(self) -> None:
        response = self.client.post("/api/maintenance/execute/AIR-01", json={})
        self.assertEqual(response.status_code, 200)
        payload = self.client.get("/api/dashboard").json()
        asset = next(item for item in payload["assets"] if item["asset_id"] == "AIR-01")
        self.assertEqual(asset["state"], "maintenance")

    def test_scenario_endpoint_returns_updated_snapshot(self) -> None:
        response = self.client.post("/api/scenarios/overload-shift", json={"duration_hours": 12})
        self.assertEqual(response.status_code, 200)
        time.sleep(0.05)
        payload = self.client.get("/api/dashboard").json()
        self.assertTrue(any(insight["source"] == "overload-shift" for insight in payload["insights"]))

    def test_sse_stream_sends_initial_snapshot(self) -> None:
        response = self.client.get("/api/events?once=true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn('"metrics"', response.text)


if __name__ == "__main__":
    unittest.main()
