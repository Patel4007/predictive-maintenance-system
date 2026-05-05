from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .analytics import PredictionResult
from .models import MaintenanceRecommendation


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ScheduleCandidate:
    asset_id: str
    asset_name: str
    state: str
    criticality: int
    maintenance_duration_hours: float
    downtime_cost_per_hour: float
    prediction: PredictionResult


class MaintenanceScheduler:
    def __init__(self, *, crew_capacity: int, slot_hours: int) -> None:
        self.crew_capacity = max(1, crew_capacity)
        self.slot_hours = max(1, slot_hours)

    def build_schedule(self, now: datetime, candidates: Sequence[ScheduleCandidate]) -> list[MaintenanceRecommendation]:
        queue: list[tuple[float, float, ScheduleCandidate, str, str, float]] = []
        for candidate in candidates:
            prediction = candidate.prediction
            if (
                candidate.state == "running"
                and prediction.risk_level == "low"
                and prediction.probability_7d < 0.22
                and prediction.remaining_useful_life_hours > 220
            ):
                continue

            priority_score = round(
                100.0
                * (
                    0.42 * prediction.probability_7d
                    + 0.18 * prediction.probability_24h
                    + 0.18 * (1.0 - prediction.health_score / 100.0)
                    + 0.14 * (candidate.criticality / 5.0)
                    + 0.08 * (1.0 if candidate.state == "failed" else 0.0)
                ),
                1,
            )
            due_hours = max(2.0, min(prediction.remaining_useful_life_hours * 0.75 if prediction.remaining_useful_life_hours else 2.0, 168.0))

            if candidate.state == "failed" or prediction.risk_level == "critical":
                priority_label = "Immediate"
                action = "Emergency diagnostic and component replacement"
            elif prediction.risk_level == "high":
                priority_label = "Urgent"
                action = "Planned outage maintenance and bearing inspection"
            elif prediction.risk_level == "medium":
                priority_label = "Planned"
                action = "Condition-based service during the next maintenance slot"
            else:
                priority_label = "Monitor"
                action = "Enhanced monitoring and lubrication check"

            queue.append((priority_score, due_hours, candidate, priority_label, action, candidate.maintenance_duration_hours))

        queue.sort(key=lambda item: (item[3] != "Immediate", -item[0], item[1], item[2].asset_name))
        windows = [
            {
                "start": now + timedelta(hours=2 + index * self.slot_hours),
                "capacity_hours": float(self.crew_capacity * self.slot_hours),
            }
            for index in range(8)
        ]

        recommendations: list[MaintenanceRecommendation] = []
        for index, (priority_score, due_hours, candidate, priority_label, action, duration_hours) in enumerate(queue, start=1):
            due_by = now + timedelta(hours=due_hours)
            assigned_start = windows[-1]["start"]
            for window in windows:
                if window["capacity_hours"] >= duration_hours and window["start"] <= due_by:
                    assigned_start = window["start"]
                    window["capacity_hours"] -= duration_hours
                    break
                if window["capacity_hours"] >= duration_hours and assigned_start > window["start"]:
                    assigned_start = window["start"]
            downtime_avoided = round(
                max(
                    duration_hours,
                    candidate.prediction.probability_7d * candidate.criticality * 5.5 + (6.0 if candidate.state == "failed" else 0.0),
                ),
                1,
            )
            cost_avoided = round(downtime_avoided * candidate.downtime_cost_per_hour, 0)
            rationale = f"{candidate.prediction.recommended_action} {candidate.prediction.drivers[0]}"

            recommendations.append(
                MaintenanceRecommendation(
                    task_id=f"WO-{candidate.asset_id}-{index:02d}",
                    asset_id=candidate.asset_id,
                    asset_name=candidate.asset_name,
                    priority_label=priority_label,
                    priority_score=priority_score,
                    action=action,
                    scheduled_start=_utc(assigned_start),
                    due_by=_utc(due_by),
                    duration_hours=round(duration_hours, 1),
                    estimated_downtime_avoided_hours=downtime_avoided,
                    estimated_cost_avoided_usd=cost_avoided,
                    predicted_failure_mode=candidate.prediction.predicted_failure_mode,
                    rationale=rationale,
                )
            )
        return recommendations
