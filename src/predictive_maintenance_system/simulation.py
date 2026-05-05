from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .analytics import MaintenancePredictor, PredictionResult
from .config import Settings
from .models import AssetSnapshot, DashboardSnapshot, FleetMetrics, HistoryPoint, Insight, ScenarioDefinition
from .scheduler import MaintenanceScheduler, ScheduleCandidate


def utc_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


@dataclass(frozen=True, slots=True)
class AssetSpec:
    asset_id: str
    name: str
    asset_type: str
    location: str
    criticality: int
    downtime_cost_per_hour: float
    maintenance_duration_hours: float
    base_vibration_mm_s: float
    base_temperature_c: float
    base_power_kw: float
    base_pressure_bar: float
    base_load_pct: float
    load_phase: float


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    timestamp: str
    vibration_mm_s: float
    temperature_c: float
    power_kw: float
    pressure_bar: float
    load_pct: float
    throughput_pct: float
    lubricant_pct: float
    anomaly_index: float


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    scenario_id: str
    name: str
    description: str
    impact: str


@dataclass(slots=True)
class AssetRuntime:
    spec: AssetSpec
    state: str = "running"
    bearing_wear: float = 0.0
    lubrication_loss: float = 0.0
    alignment_drift: float = 0.0
    pressure_loss: float = 0.0
    age_hours: float = 0.0
    total_downtime_hours: float = 0.0
    failure_count: int = 0
    last_maintenance_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    maintenance_until: datetime | None = None
    failure_until: datetime | None = None
    active_failure_mode: str | None = None
    latest_point: TelemetryPoint | None = None
    history: list[TelemetryPoint] = field(default_factory=list)
    health_history: list[float] = field(default_factory=list)
    risk_history: list[float] = field(default_factory=list)
    vibration_history: list[float] = field(default_factory=list)
    latest_prediction: PredictionResult | None = None


class PredictiveMaintenanceEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.random = random.Random(settings.random_seed)
        self.step_hours = settings.simulation_step_minutes / 60.0
        self.predictor = MaintenancePredictor()
        self.scheduler = MaintenanceScheduler(
            crew_capacity=settings.maintenance_crew_capacity,
            slot_hours=settings.maintenance_slot_hours,
        )
        self.scenario_specs = {
            "overload-shift": ScenarioSpec(
                "overload-shift",
                "Overload Shift",
                "Pushes the full fleet through a sustained high-load production run.",
                "Load, power, and vibration all climb, accelerating degradation on critical assets.",
            ),
            "lubrication-loss": ScenarioSpec(
                "lubrication-loss",
                "Lubrication Loss",
                "Simulates delayed greasing on rotating assets.",
                "Lubricant reserve falls, temperature rises, and bearing-related risk increases quickly.",
            ),
            "cooling-loop-drop": ScenarioSpec(
                "cooling-loop-drop",
                "Cooling Loop Drop",
                "Reduces plant cooling effectiveness for heat-sensitive equipment.",
                "Thermal stress increases on compressors, pumps, and hydraulic equipment.",
            ),
        }
        self.asset_specs = [
            AssetSpec("AIR-01", "Air Compressor", "compressor", "Utility Bay", 5, 4200.0, 4.0, 2.2, 54.0, 132.0, 7.6, 71.0, 0.1),
            AssetSpec("CNC-02", "CNC Spindle", "cnc spindle", "Machining Cell 2", 5, 6800.0, 6.0, 2.8, 48.0, 86.0, 5.2, 78.0, 1.1),
            AssetSpec("ROB-03", "Robot Wrist Drive", "robot actuator", "Assembly Line", 4, 3900.0, 3.5, 1.9, 44.0, 54.0, 4.5, 69.0, 2.2),
            AssetSpec("CNV-04", "Conveyor Drive", "conveyor motor", "Packing Spur", 3, 2100.0, 3.0, 1.6, 39.0, 34.0, 3.8, 73.0, 2.8),
            AssetSpec("HYD-05", "Hydraulic Press", "hydraulic press", "Forming Cell", 5, 7200.0, 5.5, 2.4, 52.0, 112.0, 9.4, 76.0, 3.4),
            AssetSpec("PMP-06", "Cooling Pump", "cooling pump", "Thermal Plant", 4, 3100.0, 4.0, 1.8, 41.0, 44.0, 6.8, 67.0, 4.1),
        ]
        self.reset()

    def reset(self) -> None:
        self.current_time = datetime.now(timezone.utc) - timedelta(
            minutes=self.settings.simulation_step_minutes * self.settings.initial_warmup_steps
        )
        self.active_scenarios: dict[str, datetime] = {}
        self.event_log: list[dict[str, Any]] = []
        self.history: list[HistoryPoint] = []
        self.runtimes = []
        for spec in self.asset_specs:
            runtime = AssetRuntime(
                spec=spec,
                bearing_wear=self.random.uniform(0.14, 0.32),
                lubrication_loss=self.random.uniform(0.08, 0.24),
                alignment_drift=self.random.uniform(0.06, 0.18),
                pressure_loss=self.random.uniform(0.04, 0.16),
                age_hours=self.random.uniform(240.0, 1400.0),
                last_maintenance_at=self.current_time - timedelta(hours=self.random.uniform(24.0, 220.0)),
            )
            self.runtimes.append(runtime)

        for _ in range(self.settings.initial_warmup_steps):
            self._advance(emit_events=False)
        self._snapshot_cache = self._build_snapshot()

    def dashboard_snapshot(self) -> DashboardSnapshot:
        return self._snapshot_cache

    def scenario_catalog(self) -> list[dict[str, str]]:
        return [
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "description": scenario.description,
                "impact": scenario.impact,
            }
            for scenario in self.scenario_specs.values()
        ]

    def inject_scenario(self, scenario_id: str, *, duration_hours: int) -> dict[str, str]:
        if scenario_id not in self.scenario_specs:
            raise ValueError(f"Unknown scenario: {scenario_id}")
        until = self.current_time + timedelta(hours=duration_hours)
        self.active_scenarios[scenario_id] = until
        scenario = self.scenario_specs[scenario_id]
        self._log_event(
            severity="warning",
            title=scenario.name,
            detail=f"{scenario.description} Duration: {duration_hours} virtual hours.",
            source=scenario_id,
        )
        self._snapshot_cache = self._build_snapshot()
        return {"status": "ok", "detail": f"{scenario.name} is active for the next {duration_hours} virtual hours."}

    def service_asset(self, asset_id: str) -> dict[str, str]:
        runtime = self._runtime(asset_id)
        if runtime.state == "maintenance":
            raise ValueError(f"{runtime.spec.name} is already in a maintenance window.")

        runtime.maintenance_until = self.current_time + timedelta(hours=runtime.spec.maintenance_duration_hours)
        runtime.failure_until = None
        runtime.state = "maintenance"
        self._log_event(
            severity="info",
            title=f"{runtime.spec.name} scheduled",
            detail=(
                f"{runtime.spec.name} was pulled into a maintenance window for "
                f"{runtime.spec.maintenance_duration_hours:.1f} hours."
            ),
            source=runtime.spec.asset_id,
        )
        self._snapshot_cache = self._build_snapshot()
        return {"status": "ok", "detail": f"{runtime.spec.name} is now in maintenance mode."}

    def step(self) -> None:
        self._advance(emit_events=True)
        self._snapshot_cache = self._build_snapshot()

    def _advance(self, *, emit_events: bool) -> None:
        self.current_time += timedelta(minutes=self.settings.simulation_step_minutes)
        self._prune_scenarios()

        for runtime in self.runtimes:
            previous_state = runtime.state
            if runtime.maintenance_until and self.current_time >= runtime.maintenance_until:
                self._complete_maintenance(runtime, emit_events=emit_events)
            if runtime.failure_until and self.current_time >= runtime.failure_until:
                self._complete_failure_repair(runtime, emit_events=emit_events)

            if runtime.state == "maintenance":
                point = self._maintenance_point(runtime)
                runtime.total_downtime_hours += self.step_hours
            elif runtime.state == "failed":
                point = self._failed_point(runtime)
                runtime.total_downtime_hours += self.step_hours
            else:
                point = self._operating_point(runtime)
                if runtime.state == "degraded":
                    runtime.total_downtime_hours += self.step_hours * 0.15

            runtime.age_hours += self.step_hours
            runtime.latest_point = point
            runtime.history.append(point)
            runtime.history = runtime.history[- self.settings.history_limit :]

            prediction = self.predictor.predict(
                runtime.spec,
                runtime.history,
                state=runtime.state,
                hours_since_maintenance=(self.current_time - runtime.last_maintenance_at).total_seconds() / 3600.0,
            )
            runtime.latest_prediction = prediction
            runtime.health_history.append(prediction.health_score)
            runtime.risk_history.append(prediction.probability_7d * 100.0)
            runtime.vibration_history.append(point.vibration_mm_s)
            runtime.health_history = runtime.health_history[- self.settings.asset_chart_points :]
            runtime.risk_history = runtime.risk_history[- self.settings.asset_chart_points :]
            runtime.vibration_history = runtime.vibration_history[- self.settings.asset_chart_points :]

            if runtime.state in {"running", "degraded"}:
                failure_mode = prediction.predicted_failure_mode
                failure_threshold = 0.0075 * self.step_hours + prediction.probability_24h * 0.013 + prediction.trend_score * 0.002
                if self.random.random() < failure_threshold and max(
                    runtime.bearing_wear,
                    runtime.lubrication_loss,
                    runtime.alignment_drift,
                    runtime.pressure_loss,
                ) > 0.82:
                    runtime.state = "failed"
                    runtime.failure_count += 1
                    runtime.active_failure_mode = failure_mode
                    runtime.failure_until = self.current_time + timedelta(hours=runtime.spec.maintenance_duration_hours * 1.4)
                    if emit_events:
                        self._log_event(
                            severity="critical",
                            title=f"{runtime.spec.name} failure",
                            detail=f"{failure_mode} crossed the failure threshold and the asset is now offline.",
                            source=runtime.spec.asset_id,
                        )

            if emit_events and previous_state != runtime.state and runtime.state == "degraded":
                self._log_event(
                    severity="warning",
                    title=f"{runtime.spec.name} degraded",
                    detail=f"{runtime.spec.name} drifted into a degraded band and now requires closer monitoring.",
                    source=runtime.spec.asset_id,
                )

    def _runtime(self, asset_id: str) -> AssetRuntime:
        for runtime in self.runtimes:
            if runtime.spec.asset_id == asset_id:
                return runtime
        raise ValueError(f"Unknown asset: {asset_id}")

    def _scenario_active(self, scenario_id: str) -> bool:
        until = self.active_scenarios.get(scenario_id)
        return until is not None and until > self.current_time

    def _scenario_effects(self, runtime: AssetRuntime) -> tuple[float, float, float]:
        stress_multiplier = 1.0
        temperature_bias = 0.0
        lubrication_bias = 0.0
        if self._scenario_active("overload-shift"):
            stress_multiplier += 0.22
        if self._scenario_active("cooling-loop-drop") and runtime.spec.asset_type in {"compressor", "hydraulic press", "cooling pump"}:
            temperature_bias += 4.8
        if self._scenario_active("lubrication-loss") and runtime.spec.asset_type in {"compressor", "cnc spindle", "conveyor motor"}:
            lubrication_bias += 0.018
        return stress_multiplier, temperature_bias, lubrication_bias

    def _operating_point(self, runtime: AssetRuntime) -> TelemetryPoint:
        spec = runtime.spec
        stress_multiplier, temperature_bias, lubrication_bias = self._scenario_effects(runtime)
        cycle_position = (
            (self.current_time.hour + self.current_time.minute / 60.0) / 24.0 * math.tau + spec.load_phase
        )
        cyclical_load = math.sin(cycle_position) * 7.5 + math.cos(cycle_position * 1.9) * 2.8
        load_pct = _clamp(spec.base_load_pct + cyclical_load + self.random.uniform(-2.5, 3.0), 42.0, 98.0)
        load_stress = max(0.0, (load_pct - 70.0) / 30.0) * stress_multiplier
        shock = self.random.uniform(0.08, 0.3) if self.random.random() < 0.12 else self.random.uniform(0.0, 0.06)

        runtime.bearing_wear = _clamp(
            runtime.bearing_wear + self.step_hours * (0.010 + 0.016 * load_stress + 0.004 * shock),
            0.0,
            1.25,
        )
        runtime.lubrication_loss = _clamp(
            runtime.lubrication_loss + self.step_hours * (0.008 + 0.013 * load_stress + lubrication_bias),
            0.0,
            1.2,
        )
        runtime.alignment_drift = _clamp(
            runtime.alignment_drift + self.step_hours * (0.006 + 0.010 * load_stress + 0.002 * shock),
            0.0,
            1.1,
        )
        runtime.pressure_loss = _clamp(
            runtime.pressure_loss + self.step_hours * (0.005 + 0.008 * load_stress + 0.002 * shock),
            0.0,
            1.0,
        )

        vibration = spec.base_vibration_mm_s * (
            1.0 + 0.72 * runtime.bearing_wear + 0.46 * runtime.alignment_drift + 0.18 * shock
        ) + self.random.uniform(-0.09, 0.12)
        temperature = (
            spec.base_temperature_c
            + 9.0 * runtime.bearing_wear
            + 10.5 * runtime.lubrication_loss
            + 3.4 * load_stress
            + temperature_bias
            + self.random.uniform(-0.7, 0.9)
        )
        power = spec.base_power_kw * (
            1.0
            + 0.14 * runtime.bearing_wear
            + 0.10 * runtime.alignment_drift
            + 0.08 * runtime.lubrication_loss
            + 0.04 * shock
        ) + self.random.uniform(-1.2, 1.3)
        pressure = spec.base_pressure_bar * (
            1.0 - 0.14 * runtime.pressure_loss - 0.05 * runtime.lubrication_loss + 0.02 * load_stress
        ) + self.random.uniform(-0.12, 0.12)
        lubricant_pct = _clamp(
            100.0 - 60.0 * runtime.lubrication_loss - 18.0 * runtime.bearing_wear + self.random.uniform(-2.5, 2.5),
            4.0,
            100.0,
        )
        throughput = _clamp(
            100.0
            - 16.0 * runtime.alignment_drift
            - 11.0 * runtime.bearing_wear
            - 9.0 * runtime.pressure_loss
            - (5.0 if self._scenario_active("overload-shift") else 0.0)
            + self.random.uniform(-2.0, 1.6),
            52.0,
            103.0,
        )
        anomaly = _clamp(
            0.26 * shock
            + 0.24 * max(vibration / max(spec.base_vibration_mm_s, 0.1) - 1.0, 0.0)
            + 0.18 * max(temperature - spec.base_temperature_c, 0.0) / 20.0
            + 0.12 * max(1.0 - pressure / max(spec.base_pressure_bar, 0.1), 0.0),
            0.0,
            1.35,
        )
        runtime.state = "degraded" if max(
            runtime.bearing_wear,
            runtime.lubrication_loss,
            runtime.alignment_drift,
            runtime.pressure_loss,
        ) > 0.74 or anomaly > 0.48 or throughput < 87.0 else "running"
        runtime.active_failure_mode = None

        return TelemetryPoint(
            timestamp=utc_timestamp(self.current_time),
            vibration_mm_s=round(vibration, 2),
            temperature_c=round(temperature, 1),
            power_kw=round(max(power, 0.1), 1),
            pressure_bar=round(max(pressure, 0.5), 2),
            load_pct=round(load_pct, 1),
            throughput_pct=round(throughput, 1),
            lubricant_pct=round(lubricant_pct, 1),
            anomaly_index=round(anomaly, 2),
        )

    def _maintenance_point(self, runtime: AssetRuntime) -> TelemetryPoint:
        spec = runtime.spec
        runtime.active_failure_mode = None
        return TelemetryPoint(
            timestamp=utc_timestamp(self.current_time),
            vibration_mm_s=round(spec.base_vibration_mm_s * 0.58, 2),
            temperature_c=round(spec.base_temperature_c - 3.6, 1),
            power_kw=round(spec.base_power_kw * 0.16, 1),
            pressure_bar=round(spec.base_pressure_bar * 0.92, 2),
            load_pct=0.0,
            throughput_pct=0.0,
            lubricant_pct=round(_clamp(92.0 - runtime.lubrication_loss * 18.0, 70.0, 98.0), 1),
            anomaly_index=0.04,
        )

    def _failed_point(self, runtime: AssetRuntime) -> TelemetryPoint:
        spec = runtime.spec
        return TelemetryPoint(
            timestamp=utc_timestamp(self.current_time),
            vibration_mm_s=round(spec.base_vibration_mm_s * (1.6 + runtime.bearing_wear * 0.6), 2),
            temperature_c=round(spec.base_temperature_c + 14.0 + runtime.lubrication_loss * 10.0, 1),
            power_kw=round(spec.base_power_kw * 0.34, 1),
            pressure_bar=round(spec.base_pressure_bar * max(0.6, 0.82 - runtime.pressure_loss * 0.2), 2),
            load_pct=0.0,
            throughput_pct=0.0,
            lubricant_pct=round(_clamp(52.0 - runtime.lubrication_loss * 16.0, 4.0, 60.0), 1),
            anomaly_index=1.18,
        )

    def _complete_maintenance(self, runtime: AssetRuntime, *, emit_events: bool) -> None:
        runtime.maintenance_until = None
        runtime.state = "running"
        runtime.bearing_wear *= 0.42
        runtime.lubrication_loss *= 0.28
        runtime.alignment_drift *= 0.55
        runtime.pressure_loss *= 0.40
        runtime.last_maintenance_at = self.current_time
        if emit_events:
            self._log_event(
                severity="info",
                title=f"{runtime.spec.name} returned to service",
                detail="Planned maintenance completed and the condition profile improved materially.",
                source=runtime.spec.asset_id,
            )

    def _complete_failure_repair(self, runtime: AssetRuntime, *, emit_events: bool) -> None:
        runtime.failure_until = None
        runtime.state = "running"
        runtime.bearing_wear *= 0.25
        runtime.lubrication_loss *= 0.18
        runtime.alignment_drift *= 0.34
        runtime.pressure_loss *= 0.30
        runtime.last_maintenance_at = self.current_time
        if emit_events:
            self._log_event(
                severity="info",
                title=f"{runtime.spec.name} repaired",
                detail="Corrective maintenance completed and the asset has rejoined production.",
                source=runtime.spec.asset_id,
            )

    def _prune_scenarios(self) -> None:
        expired = [scenario_id for scenario_id, until in self.active_scenarios.items() if until <= self.current_time]
        for scenario_id in expired:
            del self.active_scenarios[scenario_id]

    def _log_event(self, *, severity: str, title: str, detail: str, source: str) -> None:
        self.event_log.insert(
            0,
            {
                "timestamp": utc_timestamp(self.current_time),
                "severity": severity,
                "title": title,
                "detail": detail,
                "source": source,
            },
        )
        self.event_log = self.event_log[:8]

    def _build_snapshot(self) -> DashboardSnapshot:
        assets: list[AssetSnapshot] = []
        candidates: list[ScheduleCandidate] = []

        for runtime in self.runtimes:
            point = runtime.latest_point or self._maintenance_point(runtime)
            prediction = runtime.latest_prediction or self.predictor.predict(
                runtime.spec,
                runtime.history,
                state=runtime.state,
                hours_since_maintenance=(self.current_time - runtime.last_maintenance_at).total_seconds() / 3600.0,
            )
            candidates.append(
                ScheduleCandidate(
                    asset_id=runtime.spec.asset_id,
                    asset_name=runtime.spec.name,
                    state=runtime.state,
                    criticality=runtime.spec.criticality,
                    maintenance_duration_hours=runtime.spec.maintenance_duration_hours,
                    downtime_cost_per_hour=runtime.spec.downtime_cost_per_hour,
                    prediction=prediction,
                )
            )
            assets.append(
                AssetSnapshot(
                    asset_id=runtime.spec.asset_id,
                    name=runtime.spec.name,
                    asset_type=runtime.spec.asset_type,
                    location=runtime.spec.location,
                    state=runtime.state,
                    criticality=runtime.spec.criticality,
                    health_score=prediction.health_score,
                    vibration_mm_s=point.vibration_mm_s,
                    temperature_c=point.temperature_c,
                    power_kw=point.power_kw,
                    pressure_bar=point.pressure_bar,
                    load_pct=point.load_pct,
                    throughput_pct=point.throughput_pct,
                    lubricant_pct=point.lubricant_pct,
                    anomaly_score=prediction.anomaly_score,
                    probability_24h=prediction.probability_24h,
                    probability_7d=prediction.probability_7d,
                    remaining_useful_life_hours=prediction.remaining_useful_life_hours,
                    maintenance_due_in_hours=round(max(4.0, min(prediction.remaining_useful_life_hours, 240.0)), 1),
                    predicted_failure_mode=prediction.predicted_failure_mode,
                    confidence=prediction.confidence,
                    risk_level=prediction.risk_level,
                    recommended_action=prediction.recommended_action,
                    downtime_hours=round(runtime.total_downtime_hours, 1),
                    failure_count=runtime.failure_count,
                    risk_drivers=prediction.drivers,
                    recent_health=[round(value, 1) for value in runtime.health_history],
                    recent_risk=[round(value, 1) for value in runtime.risk_history],
                    recent_vibration=[round(value, 2) for value in runtime.vibration_history],
                )
            )

        schedule = self.scheduler.build_schedule(self.current_time, candidates)
        next_windows = {item.asset_id: item.scheduled_start for item in schedule}
        for asset in assets:
            asset.next_maintenance_window = next_windows.get(asset.asset_id)

        assets.sort(key=lambda item: ({"critical": 0, "high": 1, "medium": 2, "low": 3}[item.risk_level], -item.probability_7d))
        urgent_count = sum(1 for item in schedule if item.priority_label in {"Immediate", "Urgent"})
        avg_health = round(sum(asset.health_score for asset in assets) / max(len(assets), 1), 1)
        avg_probability_7d = round(sum(asset.probability_7d for asset in assets) / max(len(assets), 1), 3)
        predicted_failures = round(sum(asset.probability_7d for asset in assets), 2)
        high_risk_assets = sum(1 for asset in assets if asset.risk_level in {"high", "critical"})
        total_scheduled_hours = round(sum(item.duration_hours for item in schedule), 1)
        downtime_avoided = round(sum(item.estimated_downtime_avoided_hours for item in schedule), 1)
        cost_avoided = round(sum(item.estimated_cost_avoided_usd for item in schedule), 0)

        metrics = FleetMetrics(
            asset_count=len(assets),
            running_assets=sum(1 for asset in assets if asset.state == "running"),
            degraded_assets=sum(1 for asset in assets if asset.state == "degraded"),
            failed_assets=sum(1 for asset in assets if asset.state == "failed"),
            average_health_score=avg_health,
            average_probability_7d=avg_probability_7d,
            high_risk_assets=high_risk_assets,
            urgent_work_orders=urgent_count,
            predicted_failures_7d=predicted_failures,
            scheduled_maintenance_hours=total_scheduled_hours,
            estimated_downtime_avoided_hours=downtime_avoided,
            estimated_cost_avoided_usd=cost_avoided,
        )

        history_point = HistoryPoint(
            timestamp=utc_timestamp(self.current_time),
            average_health_score=avg_health,
            average_probability_7d=avg_probability_7d,
            high_risk_assets=high_risk_assets,
            urgent_work_orders=urgent_count,
            estimated_downtime_avoided_hours=downtime_avoided,
        )
        if not self.history or self.history[-1].timestamp != history_point.timestamp:
            self.history.append(history_point)
        self.history = self.history[- self.settings.history_limit :]

        insights = self._build_insights(assets=assets, schedule=schedule, metrics=metrics)
        return DashboardSnapshot(
            generated_at=utc_timestamp(self.current_time),
            site_name=self.settings.site_name,
            fleet_name=self.settings.fleet_name,
            update_interval_seconds=self.settings.update_interval_seconds,
            metrics=metrics,
            assets=assets,
            maintenance_schedule=schedule,
            history=self.history,
            insights=insights,
            scenarios=[ScenarioDefinition.model_validate(item) for item in self.scenario_catalog()],
        )

    def _build_insights(
        self,
        *,
        assets: list[AssetSnapshot],
        schedule: list[Any],
        metrics: FleetMetrics,
    ) -> list[Insight]:
        insights: list[Insight] = []
        for scenario_id, until in self.active_scenarios.items():
            if until <= self.current_time:
                continue
            scenario = self.scenario_specs[scenario_id]
            insights.append(
                Insight(
                    timestamp=utc_timestamp(self.current_time),
                    severity="warning",
                    title=f"{scenario.name} is active",
                    detail=scenario.impact,
                    source=scenario_id,
                )
            )
        if assets:
            lead_asset = assets[0]
            if lead_asset.risk_level in {"high", "critical"}:
                insights.append(
                    Insight(
                        timestamp=utc_timestamp(self.current_time),
                        severity="critical" if lead_asset.risk_level == "critical" else "warning",
                        title=f"{lead_asset.name} is driving the risk profile",
                        detail=(
                            f"{lead_asset.predicted_failure_mode} has a {lead_asset.probability_24h:.0%} 24-hour failure probability "
                            f"and only {lead_asset.remaining_useful_life_hours:.0f} hours of estimated useful life remaining."
                        ),
                        source=lead_asset.asset_id,
                    )
                )

        if schedule:
            first = schedule[0]
            insights.append(
                Insight(
                    timestamp=utc_timestamp(self.current_time),
                    severity="info",
                    title="Maintenance schedule optimized",
                    detail=(
                        f"{len(schedule)} work orders are sequenced with an estimated "
                        f"{metrics.estimated_downtime_avoided_hours:.1f} hours of downtime avoided."
                    ),
                    source=first.asset_id,
                )
            )

        if len(self.history) >= 8:
            past = self.history[-8]
            delta = past.average_health_score - metrics.average_health_score
            if delta >= 3.5:
                insights.append(
                    Insight(
                        timestamp=utc_timestamp(self.current_time),
                        severity="warning",
                        title="Fleet health is trending downward",
                        detail=f"Average health has fallen by {delta:.1f} points across the recent operating window.",
                        source="fleet",
                    )
                )

        for event in self.event_log[:3]:
            insights.append(Insight.model_validate(event))
        return insights[:6]
