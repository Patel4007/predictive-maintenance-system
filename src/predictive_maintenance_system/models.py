from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FleetMetrics(BaseModel):
    asset_count: int
    running_assets: int
    degraded_assets: int
    failed_assets: int
    average_health_score: float
    average_probability_7d: float
    high_risk_assets: int
    urgent_work_orders: int
    predicted_failures_7d: float
    scheduled_maintenance_hours: float
    estimated_downtime_avoided_hours: float
    estimated_cost_avoided_usd: float


class AssetSnapshot(BaseModel):
    asset_id: str
    name: str
    asset_type: str
    location: str
    state: Literal["running", "degraded", "maintenance", "failed"]
    criticality: int = Field(ge=1, le=5)
    health_score: float
    vibration_mm_s: float
    temperature_c: float
    power_kw: float
    pressure_bar: float
    load_pct: float
    throughput_pct: float
    lubricant_pct: float
    anomaly_score: float
    probability_24h: float
    probability_7d: float
    remaining_useful_life_hours: float
    maintenance_due_in_hours: float
    predicted_failure_mode: str
    confidence: float
    risk_level: Literal["low", "medium", "high", "critical"]
    recommended_action: str
    next_maintenance_window: str | None = None
    downtime_hours: float
    failure_count: int
    risk_drivers: list[str]
    recent_health: list[float]
    recent_risk: list[float]
    recent_vibration: list[float]


class MaintenanceRecommendation(BaseModel):
    task_id: str
    asset_id: str
    asset_name: str
    priority_label: Literal["Immediate", "Urgent", "Planned", "Monitor"]
    priority_score: float
    action: str
    scheduled_start: str
    due_by: str
    duration_hours: float
    estimated_downtime_avoided_hours: float
    estimated_cost_avoided_usd: float
    predicted_failure_mode: str
    rationale: str


class HistoryPoint(BaseModel):
    timestamp: str
    average_health_score: float
    average_probability_7d: float
    high_risk_assets: int
    urgent_work_orders: int
    estimated_downtime_avoided_hours: float


class Insight(BaseModel):
    timestamp: str
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    source: str


class ScenarioDefinition(BaseModel):
    scenario_id: str
    name: str
    description: str
    impact: str


class DashboardSnapshot(BaseModel):
    generated_at: str
    site_name: str
    fleet_name: str
    update_interval_seconds: float
    metrics: FleetMetrics
    assets: list[AssetSnapshot]
    maintenance_schedule: list[MaintenanceRecommendation]
    history: list[HistoryPoint]
    insights: list[Insight]
    scenarios: list[ScenarioDefinition]


class ScenarioTriggerRequest(BaseModel):
    duration_hours: int = Field(default=16, ge=1, le=96)


class ActionResponse(BaseModel):
    status: str
    detail: str
