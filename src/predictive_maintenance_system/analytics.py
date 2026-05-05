from __future__ import annotations

from dataclasses import dataclass
from math import exp
from statistics import mean
from typing import Sequence


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + exp(-value))


def _slope(values: Sequence[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    x_mean = (count - 1) / 2.0
    y_mean = sum(values) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    if denominator == 0:
        return 0.0
    numerator = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
    return numerator / denominator


def _series(history: Sequence[object], attr: str, sample_size: int) -> list[float]:
    if not history:
        return []
    subset = history[-sample_size:]
    return [float(getattr(point, attr, 0.0)) for point in subset]


@dataclass(frozen=True, slots=True)
class PredictionResult:
    health_score: float
    probability_24h: float
    probability_7d: float
    remaining_useful_life_hours: float
    predicted_failure_mode: str
    confidence: float
    anomaly_score: float
    trend_score: float
    risk_level: str
    drivers: list[str]
    recommended_action: str


class MaintenancePredictor:
    """Lightweight ensemble of statistical risk, trend, and RUL estimators."""

    def predict(
        self,
        spec: object,
        history: Sequence[object],
        *,
        state: str,
        hours_since_maintenance: float,
    ) -> PredictionResult:
        recent = history[-24:] if history else []
        baseline_vibration = max(float(getattr(spec, "base_vibration_mm_s", 1.0)), 0.1)
        baseline_temperature = float(getattr(spec, "base_temperature_c", 35.0))
        baseline_power = max(float(getattr(spec, "base_power_kw", 1.0)), 0.1)
        baseline_pressure = max(float(getattr(spec, "base_pressure_bar", 1.0)), 0.1)
        criticality = int(getattr(spec, "criticality", 3))
        asset_type = str(getattr(spec, "asset_type", "asset"))

        vibration_values = _series(recent, "vibration_mm_s", 24)
        temperature_values = _series(recent, "temperature_c", 24)
        power_values = _series(recent, "power_kw", 24)
        pressure_values = _series(recent, "pressure_bar", 24)
        load_values = _series(recent, "load_pct", 24)
        throughput_values = _series(recent, "throughput_pct", 24)
        lubricant_values = _series(recent, "lubricant_pct", 24)
        anomaly_values = _series(recent, "anomaly_index", 24)

        vibration_ratio = mean(vibration_values) / baseline_vibration if vibration_values else 1.0
        temperature_delta = mean(temperature_values) - baseline_temperature if temperature_values else 0.0
        power_ratio = mean(power_values) / baseline_power if power_values else 1.0
        pressure_ratio = mean(pressure_values) / baseline_pressure if pressure_values else 1.0
        load_stress = max(0.0, (mean(load_values) - 70.0) / 30.0) if load_values else 0.0
        throughput_drop = max(0.0, 100.0 - mean(throughput_values)) / 100.0 if throughput_values else 0.0
        lubricant_drop = max(0.0, 100.0 - mean(lubricant_values)) / 100.0 if lubricant_values else 0.0
        anomaly_score = _clip(mean(anomaly_values) if anomaly_values else 0.0, 0.0, 1.5)

        vibration_slope = _slope(vibration_values[-12:]) / baseline_vibration if vibration_values else 0.0
        temperature_slope = _slope(temperature_values[-12:]) / 10.0 if temperature_values else 0.0
        throughput_slope = _slope(throughput_values[-12:]) / 25.0 if throughput_values else 0.0
        maintenance_pressure = _clip(hours_since_maintenance / 260.0, 0.0, 1.5)

        degradation_index = (
            0.32 * max(vibration_ratio - 1.0, 0.0)
            + 0.18 * max(temperature_delta / 18.0, 0.0)
            + 0.12 * max(power_ratio - 1.0, 0.0) * 1.4
            + 0.14 * max(1.0 - pressure_ratio, 0.0) * 1.3
            + 0.16 * lubricant_drop
            + 0.14 * throughput_drop
            + 0.12 * anomaly_score
            + 0.10 * maintenance_pressure
        )
        trend_score = _clip(
            1.2 * max(vibration_slope, 0.0)
            + 0.9 * max(temperature_slope, 0.0)
            + 0.7 * max(-throughput_slope, 0.0),
            0.0,
            1.8,
        )
        criticality_factor = criticality / 5.0

        state_bias = {"running": 0.0, "degraded": 0.45, "maintenance": 0.2, "failed": 1.6}.get(state, 0.0)
        probability_24h = _clip(
            _sigmoid(-3.9 + 3.7 * degradation_index + 1.7 * trend_score + 0.9 * criticality_factor + 0.8 * load_stress + state_bias),
            0.01,
            0.99,
        )
        probability_7d = _clip(
            _sigmoid(-2.2 + 3.1 * degradation_index + 1.2 * trend_score + 0.7 * criticality_factor + 0.6 * maintenance_pressure + 0.4 * load_stress + state_bias),
            0.02,
            0.995,
        )
        health_score = _clip(
            100.0
            - degradation_index * 54.0
            - trend_score * 11.0
            - criticality_factor * 5.0
            - (22.0 if state == "failed" else 0.0)
            - (8.0 if state == "maintenance" else 0.0),
            5.0,
            99.0,
        )

        mode_scores = {
            "Bearing wear": 1.5 * max(vibration_ratio - 1.0, 0.0) + 0.6 * max(temperature_delta / 18.0, 0.0) + 0.5 * anomaly_score,
            "Lubrication loss": 1.3 * lubricant_drop + 0.9 * max(temperature_delta / 18.0, 0.0) + 0.45 * max(power_ratio - 1.0, 0.0),
            "Alignment drift": 1.1 * max(vibration_ratio - 1.0, 0.0) + 0.9 * throughput_drop + 0.45 * max(power_ratio - 1.0, 0.0),
            "Pressure leak": 1.2 * max(1.0 - pressure_ratio, 0.0) + 0.7 * throughput_drop + 0.4 * anomaly_score,
        }
        if "pump" in asset_type or "hydraulic" in asset_type:
            mode_scores["Pressure leak"] += 0.25
        if "robot" in asset_type or "conveyor" in asset_type:
            mode_scores["Alignment drift"] += 0.15
        predicted_failure_mode = max(mode_scores, key=mode_scores.get)

        burn_rate = 0.045 + degradation_index * 0.14 + trend_score * 0.08 + probability_24h * 0.18
        heuristic_rul = 240.0 * max(0.05, 1.05 - degradation_index) / max(burn_rate, 0.05)
        confidence = _clip(0.56 + min(len(history), 48) / 140.0 + min(abs(vibration_slope) + abs(temperature_slope), 0.14), 0.56, 0.94)
        remaining_useful_life_hours = 0.0 if state == "failed" else _clip(heuristic_rul * (1.0 - 0.35 * probability_24h), 8.0, 720.0)

        if state == "failed" or probability_24h >= 0.8 or remaining_useful_life_hours <= 18:
            risk_level = "critical"
            recommended_action = "Plan an immediate intervention and hold the asset before the next peak-load window."
        elif probability_7d >= 0.65 or remaining_useful_life_hours <= 72:
            risk_level = "high"
            recommended_action = "Schedule a maintenance outage within the next operating shift."
        elif probability_7d >= 0.35 or remaining_useful_life_hours <= 168:
            risk_level = "medium"
            recommended_action = "Bundle corrective work into the next planned maintenance window."
        else:
            risk_level = "low"
            recommended_action = "Keep the asset in service and continue condition monitoring."

        driver_candidates = [
            (max(vibration_ratio - 1.0, 0.0), f"Vibration is {vibration_ratio:.2f}x above the baseline signature."),
            (max(temperature_delta, 0.0), f"Temperature is elevated by {temperature_delta:.1f}°C over the nominal band."),
            (lubricant_drop, f"Lubricant reserve has fallen to roughly {100.0 - lubricant_drop * 100.0:.0f}% of target."),
            (max(1.0 - pressure_ratio, 0.0), f"Pressure performance is trailing baseline by {(1.0 - pressure_ratio) * 100.0:.0f}%."),
            (throughput_drop, f"Throughput efficiency has slipped by {throughput_drop * 100.0:.0f}% from target."),
            (trend_score, "Short-term trend analysis shows the condition indicators are still worsening."),
        ]
        driver_candidates.sort(key=lambda item: item[0], reverse=True)
        drivers = [message for score, message in driver_candidates if score > 0.04][:3]
        if not drivers:
            drivers = ["Signals remain inside the expected operating envelope."]

        return PredictionResult(
            health_score=round(health_score, 1),
            probability_24h=round(probability_24h, 3),
            probability_7d=round(probability_7d, 3),
            remaining_useful_life_hours=round(remaining_useful_life_hours, 1),
            predicted_failure_mode=predicted_failure_mode,
            confidence=round(confidence, 2),
            anomaly_score=round(anomaly_score, 2),
            trend_score=round(trend_score, 2),
            risk_level=risk_level,
            drivers=drivers,
            recommended_action=recommended_action,
        )
