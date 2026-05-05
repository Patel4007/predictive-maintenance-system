from __future__ import annotations

import os
from dataclasses import dataclass


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


@dataclass(slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8010
    site_name: str = "Helix Works"
    fleet_name: str = "Predictive Maintenance Lab"
    update_interval_seconds: float = 1.0
    simulation_step_minutes: int = 15
    history_limit: int = 120
    asset_chart_points: int = 20
    random_seed: int = 11
    initial_warmup_steps: int = 96
    maintenance_crew_capacity: int = 2
    maintenance_slot_hours: int = 8
    default_scenario_duration_hours: int = 16
    initial_fleet_running: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("PDM_HOST", "127.0.0.1"),
            port=_read_int("PDM_PORT", 8010),
            site_name=os.getenv("PDM_SITE_NAME", "Helix Works"),
            fleet_name=os.getenv("PDM_FLEET_NAME", "Predictive Maintenance Lab"),
            update_interval_seconds=_read_float("PDM_UPDATE_INTERVAL_SECONDS", 1.0),
            simulation_step_minutes=_read_int("PDM_SIMULATION_STEP_MINUTES", 15),
            history_limit=_read_int("PDM_HISTORY_LIMIT", 120),
            asset_chart_points=_read_int("PDM_ASSET_CHART_POINTS", 20),
            random_seed=_read_int("PDM_RANDOM_SEED", 11),
            initial_warmup_steps=_read_int("PDM_INITIAL_WARMUP_STEPS", 96),
            maintenance_crew_capacity=_read_int("PDM_MAINTENANCE_CREW_CAPACITY", 2),
            maintenance_slot_hours=_read_int("PDM_MAINTENANCE_SLOT_HOURS", 8),
            default_scenario_duration_hours=_read_int("PDM_DEFAULT_SCENARIO_DURATION_HOURS", 16),
            initial_fleet_running=_read_bool("PDM_INITIAL_FLEET_RUNNING", True),
        )
