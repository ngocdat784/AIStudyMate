from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.machine_intelligence_service import is_machine_failure
from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    ManufacturingRecord,
    normalize_manufacturing_dataframe,
)
from app.services.multi_file_service import _parse_numeric


_MIN_OBSERVED_PERIODS = 15
_MIN_FEATURE_ROWS = 8
_FEATURE_WINDOW = 7
_FORECAST_METHODS = ("last_observation", "rolling_mean_7")


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _sum_if_present(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) if present else None


def _unique_duration(
    records: list[ManufacturingRecord],
    field: str,
) -> float | None:
    seen: set[tuple[Any, ...]] = set()
    values: list[float] = []
    for record in records:
        value = getattr(record, field)
        if value is None:
            continue
        key = (
            record.machine,
            record.line,
            record.shift,
            record.production_start,
            record.production_end,
            float(value),
        )
        if not any(key[:-1]):
            key += (record.source_row,)
        if key in seen:
            continue
        seen.add(key)
        values.append(float(value))
    return sum(values) if values else None


def _quality_rate(records: list[ManufacturingRecord]) -> float | None:
    pairs: list[tuple[float, float]] = []
    for record in records:
        if (
            record.defective_units is not None
            and record.inspected_quantity is not None
        ):
            pairs.append(
                (record.defective_units, record.inspected_quantity)
            )
        elif record.defects is not None and record.production > 0:
            pairs.append((record.defects, record.production))
    denominator = sum(total for _, total in pairs)
    defects = sum(value for value, _ in pairs)
    if denominator <= 0 or defects > denominator:
        return None
    return defects / denominator * 100


def _valid_production_source_rows(
    dataframe: pd.DataFrame,
    dataset: ManufacturingDataset,
) -> set[int]:
    production_source = next(
        (
            source
            for source, target in dataset.column_mapping.items()
            if target == "Production"
        ),
        None,
    )
    if production_source is None:
        return set()
    valid_rows: set[int] = set()
    for record in dataset.records:
        row_index = record.source_row - 2
        if not 0 <= row_index < len(dataframe.index):
            continue
        value, issue = _parse_numeric(
            dataframe.iloc[row_index][production_source]
        )
        if value is not None and issue is None:
            valid_rows.add(record.source_row)
    return valid_rows


def _daily_series(
    dataset: ManufacturingDataset,
    valid_production_rows: set[int],
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, bool],
]:
    records = dataset.records
    by_date: dict[date, list[ManufacturingRecord]] = defaultdict(list)
    source_timezone = ZoneInfo(dataset.source_timezone)
    for record in records:
        if record.timestamp is not None:
            by_date[
                record.timestamp.astimezone(source_timezone).date()
            ].append(record)

    mapped_fields = set(dataset.column_mapping.values())
    has_production = "Production" in mapped_fields
    has_downtime = "Downtime" in mapped_fields
    has_machine_state = bool(
        {"Machine Status", "Downtime Type"}.intersection(mapped_fields)
    )
    has_unplanned_flag = (
        "Downtime Planned" in mapped_fields and has_downtime
    )
    daily_rows: list[dict[str, Any]] = []
    material_metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    material_stocks: dict[str, float] = {}
    material_stock_dates: dict[str, date] = {}

    for day, day_records in sorted(by_date.items()):
        production_values = [
            record.production
            if has_production and record.source_row in valid_production_rows
            else None
            for record in day_records
        ]
        production = _sum_if_present(production_values)
        planned = _sum_if_present(
            [record.planned_production for record in day_records]
        )
        operating = _unique_duration(day_records, "operating_time_minutes")
        available = _unique_duration(day_records, "available_time_minutes")
        downtime = (
            _sum_if_present([record.downtime_minutes for record in day_records])
            if has_downtime
            else None
        )
        defect_rate = _quality_rate(day_records)
        throughput = (
            production / (operating / 60)
            if production is not None and operating is not None and operating > 0
            else None
        )
        performance = (
            production / planned * 100
            if production is not None and planned is not None and planned > 0
            else None
        )
        labeled_machine_rows = [
            record
            for record in day_records
            if record.machine_status or record.downtime_type
        ]
        failure_count = (
            float(sum(is_machine_failure(record) for record in labeled_machine_rows))
            if has_machine_state and labeled_machine_rows
            else None
        )
        unplanned_events = (
            float(
                sum(
                    record.downtime_minutes is not None
                    and record.downtime_minutes > 0
                    and record.downtime_planned is False
                    for record in day_records
                )
            )
            if has_unplanned_flag
            else None
        )
        row = {
            "period": day.isoformat(),
            "production": production,
            "planned_production": planned,
            "throughput": throughput,
            "performance_percent": performance,
            "defect_rate_percent": defect_rate,
            "downtime_minutes": downtime,
            "available_time_minutes": available,
            "machine_failure_count": failure_count,
            "unplanned_downtime_events": unplanned_events,
        }
        daily_rows.append(row)

        grouped_materials: dict[
            tuple[str, str | None], list[ManufacturingRecord]
        ] = defaultdict(list)
        for record in day_records:
            if record.material:
                grouped_materials[
                    (record.material, record.material_unit)
                ].append(record)
        for (material, unit), material_records in grouped_materials.items():
            consumed = _sum_if_present(
                [record.actual_consumption for record in material_records]
            )
            loss_values = [
                (
                    record.material_loss_quantity
                    if record.material_loss_quantity is not None
                    else record.scrap_material_quantity
                )
                for record in material_records
                if record.actual_consumption is not None
            ]
            loss = _sum_if_present(loss_values)
            key = f"{material}|{unit or 'unknown'}"
            material_metrics[key].append(
                {
                    "period": day.isoformat(),
                    "value": consumed,
                    "material": material,
                    "unit": unit,
                }
            )
            if loss is not None:
                material_metrics[f"loss::{key}"].append(
                    {
                        "period": day.isoformat(),
                        "value": loss,
                        "material": material,
                        "unit": unit,
                    }
                )
            material_quality = _quality_rate(material_records)
            if material_quality is not None:
                material_metrics[f"quality::{key}"].append(
                    {
                        "period": day.isoformat(),
                        "value": material_quality,
                        "material": material,
                        "unit": unit,
                    }
                )
            stock_records = [
                record
                for record in material_records
                if record.material_stock_quantity is not None
            ]
            if stock_records and unit is not None:
                latest_stock_record = max(
                    stock_records,
                    key=lambda record: record.source_row,
                )
                stock_key = f"{material}|{unit or 'unknown'}"
                if stock_key not in material_stock_dates or day >= material_stock_dates[stock_key]:
                    material_stocks[key] = float(
                        latest_stock_record.material_stock_quantity or 0
                    )
                    material_stock_dates[stock_key] = day

    stock_rows = [
        {
            "material_key": key,
            "material": key.split("|", maxsplit=1)[0],
            "unit": key.split("|", maxsplit=1)[1],
            "stock": stock,
            "as_of": material_stock_dates[key].isoformat(),
        }
        for key, stock in material_stocks.items()
    ]
    return daily_rows, material_metrics, stock_rows, {
        "has_production": has_production,
        "has_downtime": has_downtime,
        "has_machine_state": has_machine_state,
        "has_unplanned_flag": has_unplanned_flag,
    }


def _build_feature_rows(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(_FEATURE_WINDOW, len(observations)):
        history = observations[index - _FEATURE_WINDOW:index]
        values = [float(item["value"]) for item in history]
        target = observations[index]
        if any(not math.isfinite(value) for value in values):
            continue
        previous_date = date.fromisoformat(history[-1]["period"])
        target_date = date.fromisoformat(target["period"])
        rows.append(
            {
                "target_period": target["period"],
                "feature_source_end_period": history[-1]["period"],
                "target": float(target["value"]),
                "features": {
                    "lag_1_observed_period": values[-1],
                    "lag_7_observed_periods": values[0],
                    "rolling_mean_7_observed_periods": statistics.fmean(values),
                    "rolling_std_7_observed_periods": statistics.pstdev(values),
                    "days_since_previous_observation": (
                        target_date - previous_date
                    ).days,
                },
                "observation_index": index,
            }
        )
    return rows


def _split_ranges(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    train_end = max(4, int(count * 0.6))
    validation_end = max(train_end + 2, int(count * 0.8))
    validation_end = min(validation_end, count - 2)
    splits = {
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "test": rows[validation_end:],
    }
    return {
        name: {
            "count": len(items),
            "start": items[0]["target_period"] if items else None,
            "end": items[-1]["target_period"] if items else None,
        }
        for name, items in splits.items()
    }


def _predict_one_step(method: str, history: list[float]) -> float:
    if not history:
        return 0.0
    if method == "rolling_mean_7":
        return statistics.fmean(history[-_FEATURE_WINDOW:])
    return history[-1]


def _bounded_prediction(metric: str, value: float) -> float:
    if metric == "defect_rate_percent":
        return min(100.0, max(0.0, value))
    return max(0.0, value)


def _error_metrics(
    actual: list[float],
    predicted: list[float],
) -> dict[str, float | int | None]:
    if not actual:
        return {
            "sample_count": 0,
            "mae": None,
            "rmse": None,
            "smape_percent": None,
        }
    errors = [prediction - observed for observed, prediction in zip(actual, predicted)]
    absolute_errors = [abs(error) for error in errors]
    smape_terms = [
        abs(prediction - observed)
        / ((abs(observed) + abs(prediction)) / 2)
        * 100
        for observed, prediction in zip(actual, predicted)
        if abs(observed) + abs(prediction) > 0
    ]
    return {
        "sample_count": len(actual),
        "mae": _rounded(statistics.fmean(absolute_errors)),
        "rmse": _rounded(math.sqrt(statistics.fmean(error * error for error in errors))),
        "smape_percent": _rounded(
            statistics.fmean(smape_terms) if smape_terms else 0.0
        ),
    }


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[position]


def _metric_drift(observations: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["value"]) for item in observations]
    if len(values) < 15:
        return {
            "status": "insufficient_data",
            "baseline_count": max(0, len(values) - 7),
            "recent_count": min(7, len(values)),
            "relative_change_percent": None,
            "standardized_shift": None,
        }
    baseline = values[:-7]
    recent = values[-7:]
    baseline_mean = statistics.fmean(baseline)
    recent_mean = statistics.fmean(recent)
    baseline_std = statistics.pstdev(baseline)
    difference = recent_mean - baseline_mean
    standardized_shift = (
        difference / baseline_std if baseline_std > 0 else None
    )
    detected = (
        abs(standardized_shift) >= 2
        if standardized_shift is not None
        else difference != 0
    )
    return {
        "status": "drift_signal" if detected else "no_drift_signal",
        "baseline_count": len(baseline),
        "recent_count": len(recent),
        "baseline_mean": _rounded(baseline_mean),
        "recent_mean": _rounded(recent_mean),
        "relative_change_percent": _rounded(
            difference / abs(baseline_mean) * 100
            if baseline_mean != 0
            else None
        ),
        "standardized_shift": _rounded(standardized_shift),
        "method": "last_7_observed_periods_vs_prior_standard_deviation_2_screen",
    }


def _forecast_metric(
    metric: str,
    observations: list[dict[str, Any]],
    *,
    horizon: int,
    coverage_percent: float,
) -> dict[str, Any]:
    clean = [
        {"period": item["period"], "value": float(item["value"])}
        for item in observations
        if item["value"] is not None and math.isfinite(float(item["value"]))
    ]
    rows = _build_feature_rows(clean)
    reasons: list[str] = []
    if len(clean) < _MIN_OBSERVED_PERIODS:
        reasons.append(
            f"Need at least {_MIN_OBSERVED_PERIODS} valid observations; "
            f"received {len(clean)}."
        )
    if len(rows) < _MIN_FEATURE_ROWS:
        reasons.append(
            f"Need at least {_MIN_FEATURE_ROWS} leakage-safe feature rows; "
            f"received {len(rows)}."
        )
    if coverage_percent < 50:
        reasons.append(
            "Observed dates cover less than half of the calendar history."
        )
    split = _split_ranges(rows)
    if any(split[name]["count"] < count for name, count in (
        ("train", 4),
        ("validation", 2),
        ("test", 2),
    )):
        reasons.append(
            "Chronological backtesting needs at least four train, two validation, "
            "and two test feature rows."
        )
    if reasons:
        return {
            "metric": metric,
            "status": "insufficient_data",
            "forecast": [],
            "observed_count": len(clean),
            "feature_row_count": len(rows),
            "temporal_split": split,
            "reason": " ".join(reasons),
            "model_type": "none",
            "backtest": None,
            "prediction_interval": {
                "status": "insufficient_data",
                "method": "empirical_90_percentile_absolute_test_error",
            },
            "drift": _metric_drift(clean),
        }

    train_end = split["train"]["count"]
    validation_end = train_end + split["validation"]["count"]
    validation_rows = rows[train_end:validation_end]
    validation_errors: dict[str, list[float]] = {
        method: [] for method in _FORECAST_METHODS
    }
    for method in _FORECAST_METHODS:
        for row in validation_rows:
            index = int(row["observation_index"])
            history = [item["value"] for item in clean[:index]]
            prediction = _bounded_prediction(
                metric,
                _predict_one_step(method, history),
            )
            validation_errors[method].append(
                abs(prediction - float(row["target"]))
            )
    chosen_method = min(
        _FORECAST_METHODS,
        key=lambda method: statistics.fmean(validation_errors[method]),
    )
    test_rows = rows[validation_end:]
    test_actual: list[float] = []
    test_predictions: list[float] = []
    test_errors: list[float] = []
    for row in test_rows:
        index = int(row["observation_index"])
        actual = float(row["target"])
        prediction = _bounded_prediction(
            metric,
            _predict_one_step(
                chosen_method,
                [item["value"] for item in clean[:index]],
            ),
        )
        test_actual.append(actual)
        test_predictions.append(prediction)
        test_errors.append(abs(actual - prediction))
    backtest = _error_metrics(test_actual, test_predictions)
    interval_width = (
        _quantile(test_errors, 0.9)
        if len(test_errors) >= 5
        else None
    )

    history = [item["value"] for item in clean]
    forecast_rows: list[dict[str, Any]] = []
    last_date = date.fromisoformat(clean[-1]["period"])
    for step in range(1, horizon + 1):
        prediction = _bounded_prediction(
            metric,
            _predict_one_step(chosen_method, history),
        )
        interval = None
        if interval_width is not None:
            width = interval_width * math.sqrt(step)
            lower = max(0.0, prediction - width)
            upper = prediction + width
            if metric == "defect_rate_percent":
                upper = min(100.0, upper)
            interval = {
                "lower": _rounded(lower),
                "upper": _rounded(upper),
                "level_percent": 90,
                "method": "holdout_absolute_error_scaled_by_sqrt_horizon",
                "coverage_guaranteed": False,
            }
        forecast_rows.append(
            {
                "period": (last_date + timedelta(days=step)).isoformat(),
                "value": _rounded(prediction),
                "prediction_interval": interval,
            }
        )
        history.append(prediction)

    score = backtest["smape_percent"]
    confidence = (
        "high"
        if len(test_rows) >= 8 and score is not None and score <= 20
        else "medium"
        if len(test_rows) >= 4 and score is not None and score <= 40
        else "low"
    )
    return {
        "metric": metric,
        "status": "forecasted",
        "forecast": forecast_rows,
        "observed_count": len(clean),
        "feature_row_count": len(rows),
        "temporal_split": split,
        "model_type": "statistical_baseline",
        "selected_method": chosen_method,
        "selection_rule": "lowest_validation_mae",
        "backtest": backtest,
        "prediction_interval": {
            "status": "available" if interval_width is not None else "insufficient_data",
            "method": "empirical_90_percentile_absolute_test_error",
            "holdout_error_count": len(test_errors),
            "coverage_guaranteed": False,
        },
        "confidence_assessment": {
            "level": confidence,
            "is_probability": False,
            "basis": "chronological_test_smape_and_test_sample_count",
            "test_smape_percent": score,
            "test_sample_count": len(test_rows),
        },
        "drift": _metric_drift(clean),
    }


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _machine_failure_assessment(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible = [
        item
        for item in observations
        if item["value"] is not None
    ]
    failures = sum(float(item["value"]) > 0 for item in eligible)
    if len(eligible) < 20 or failures < 3:
        return {
            "status": "insufficient_data",
            "observed_period_count": len(eligible),
            "failure_period_count": failures,
            "empirical_failure_probability_percent": None,
            "interval_95_percent": None,
            "interpretation": "Historical frequency only; not a calibrated prediction.",
        }
    lower, upper = _wilson_interval(int(failures), len(eligible))
    return {
        "status": "empirical_frequency_available",
        "observed_period_count": len(eligible),
        "failure_period_count": failures,
        "empirical_failure_probability_percent": _rounded(
            failures / len(eligible) * 100
        ),
        "interval_95_percent": {
            "lower": _rounded(lower * 100),
            "upper": _rounded(upper * 100),
            "method": "wilson_score_interval",
        },
        "interpretation": "Historical failure-day frequency, not causal or calibrated risk.",
    }


def _machine_failure_by_machine(
    dataset: ManufacturingDataset,
) -> list[dict[str, Any]]:
    source_timezone = ZoneInfo(dataset.source_timezone)
    observed: dict[str, dict[date, bool]] = defaultdict(dict)
    for record in dataset.records:
        if (
            record.machine is None
            or record.timestamp is None
            or not (record.machine_status or record.downtime_type)
        ):
            continue
        period = record.timestamp.astimezone(source_timezone).date()
        observed[record.machine][period] = (
            observed[record.machine].get(period, False)
            or is_machine_failure(record)
        )

    assessments = []
    for machine, periods in sorted(observed.items()):
        total = len(periods)
        failures = sum(periods.values())
        if total < 20 or failures < 3:
            assessments.append(
                {
                    "machine": machine,
                    "status": "insufficient_data",
                    "observed_period_count": total,
                    "failure_period_count": failures,
                    "empirical_failure_probability_percent": None,
                    "interval_95_percent": None,
                }
            )
            continue
        lower, upper = _wilson_interval(failures, total)
        assessments.append(
            {
                "machine": machine,
                "status": "empirical_frequency_available",
                "observed_period_count": total,
                "failure_period_count": failures,
                "empirical_failure_probability_percent": _rounded(
                    failures / total * 100
                ),
                "interval_95_percent": {
                    "lower": _rounded(lower * 100),
                    "upper": _rounded(upper * 100),
                    "method": "wilson_score_interval",
                },
            }
        )
    return assessments


def _material_shortage_assessment(
    material_forecasts: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stocks = {
        row["material_key"]: row
        for row in stock_rows
    }
    results: list[dict[str, Any]] = []
    for forecast in material_forecasts:
        stock = stocks.get(forecast["material_key"])
        future_demand = (
            sum(float(item["value"] or 0) for item in forecast["forecast"])
            if forecast["status"] == "forecasted"
            else None
        )
        if stock is None:
            results.append(
                {
                    "material": forecast["material"],
                    "unit": forecast["unit"],
                    "status": "insufficient_data",
                    "reason": "Material Stock is required to assess shortage.",
                }
            )
            continue
        if future_demand is None:
            results.append(
                {
                    "material": forecast["material"],
                    "unit": forecast["unit"],
                    "status": "insufficient_data",
                    "reason": "Consumption forecast is unavailable.",
                }
            )
            continue
        remaining = float(stock["stock"]) - future_demand
        results.append(
            {
                "material": forecast["material"],
                "unit": forecast["unit"],
                "as_of": stock["as_of"],
                "available_stock": _rounded(float(stock["stock"])),
                "forecast_horizon_consumption": _rounded(future_demand),
                "projected_remaining_stock": _rounded(remaining),
                "status": "shortage_signal" if remaining < 0 else "no_shortage_signal",
                "shortage_quantity": _rounded(max(0.0, -remaining)),
                "method": "current_stock_minus_statistical_horizon_consumption",
                "causality_claimed": False,
            }
        )
    return results


def _scenario_simulation(
    forecasts: dict[str, Any],
    *,
    capacity_change_percent: float,
    downtime_reduction_percent: float,
    material_shortage_percent: float,
    quality_improvement_percentage_points: float,
) -> dict[str, Any]:
    def forecast_sum(metric: str) -> float | None:
        result = forecasts.get(metric)
        if not result or result["status"] != "forecasted":
            return None
        return sum(float(row["value"] or 0) for row in result["forecast"])

    production = forecast_sum("production")
    downtime = forecast_sum("downtime_minutes")
    available = forecast_sum("available_time_minutes")
    defect_rate_forecast = forecasts.get("defect_rate_percent")
    defect_rate = (
        statistics.fmean(
            float(row["value"])
            for row in defect_rate_forecast["forecast"]
        )
        if defect_rate_forecast
        and defect_rate_forecast["status"] == "forecasted"
        else None
    )
    capacity_output = (
        production * (1 + capacity_change_percent / 100)
        if production is not None
        else None
    )
    runtime_scenario = None
    if (
        production is not None
        and downtime is not None
        and available is not None
        and available > downtime
    ):
        base_runtime = available - downtime
        adjusted_downtime = downtime * (
            1 - downtime_reduction_percent / 100
        )
        adjusted_runtime = available - adjusted_downtime
        runtime_scenario = production * adjusted_runtime / base_runtime
    quality_rate_after = (
        max(0.0, defect_rate - quality_improvement_percentage_points)
        if defect_rate is not None
        else None
    )
    return {
        "method": "deterministic_sensitivity_not_a_causal_or_capacity_model",
        "inputs": {
            "capacity_change_percent": capacity_change_percent,
            "downtime_reduction_percent": downtime_reduction_percent,
            "material_shortage_percent": material_shortage_percent,
            "quality_improvement_percentage_points": (
                quality_improvement_percentage_points
            ),
        },
        "scenarios": {
            "production_capacity": {
                "status": "simulated" if capacity_output is not None else "insufficient_data",
                "forecast_production": _rounded(capacity_output),
                "assumption": "Production scales linearly with the entered capacity change.",
            },
            "machine_downtime_impact": {
                "status": "simulated" if runtime_scenario is not None else "insufficient_data",
                "forecast_production": _rounded(runtime_scenario),
                "assumption": (
                    "Production scales with operating time while availability and "
                    "other factors stay fixed."
                ),
            },
            "material_shortage_impact": {
                "status": "simulated" if production is not None else "insufficient_data",
                "forecast_production": _rounded(
                    production * (1 - material_shortage_percent / 100)
                    if production is not None
                    else None
                ),
                "assumption": "Entered shortage percentage reduces output linearly.",
            },
            "quality_improvement": {
                "status": "simulated" if defect_rate is not None else "insufficient_data",
                "forecast_defect_rate_percent": _rounded(quality_rate_after),
                "forecast_good_units": _rounded(
                    production * (1 - quality_rate_after / 100)
                    if production is not None and quality_rate_after is not None
                    else None
                ),
                "assumption": (
                    "Defect rate changes by the entered percentage points; "
                    "production volume stays fixed."
                ),
            },
        },
    }


def analyze_predictive_intelligence(
    dataframe: pd.DataFrame,
    *,
    column_mapping: dict[str, str] | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    cycle_time_unit: str = "seconds",
    horizon: int = 7,
    defect_threshold_percent: float = 5.0,
    capacity_change_percent: float = 0.0,
    downtime_reduction_percent: float = 0.0,
    material_shortage_percent: float = 0.0,
    quality_improvement_percentage_points: float = 0.0,
) -> dict[str, Any]:
    if not 1 <= horizon <= 30:
        raise ValueError("Forecast horizon must be between 1 and 30 periods.")
    if not 0 <= defect_threshold_percent <= 100:
        raise ValueError("Defect threshold must be between 0 and 100 percent.")
    if not -100 <= capacity_change_percent <= 200:
        raise ValueError("Capacity change must be between -100 and 200 percent.")
    if not 0 <= downtime_reduction_percent <= 100:
        raise ValueError("Downtime reduction must be between 0 and 100 percent.")
    if not 0 <= material_shortage_percent <= 100:
        raise ValueError("Material shortage must be between 0 and 100 percent.")
    if not 0 <= quality_improvement_percentage_points <= 100:
        raise ValueError("Quality improvement must be between 0 and 100 points.")

    dataset: ManufacturingDataset = normalize_manufacturing_dataframe(
        dataframe,
        column_mapping=column_mapping,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        duration_unit=duration_unit,
        cycle_time_unit=cycle_time_unit,
        production_unit=production_unit,
        require_production=False,
    )
    valid_production_rows = _valid_production_source_rows(dataframe, dataset)
    daily_rows, material_metrics, material_stock_rows, source_flags = _daily_series(
        dataset,
        valid_production_rows,
    )
    date_values = [
        date.fromisoformat(item["period"])
        for item in daily_rows
    ]
    calendar_days = (
        (max(date_values) - min(date_values)).days + 1
        if date_values
        else 0
    )
    coverage_percent = (
        len(date_values) / calendar_days * 100 if calendar_days else 0.0
    )
    metrics: dict[str, list[dict[str, Any]]] = {
        metric: [
            {"period": row["period"], "value": row[metric]}
            for row in daily_rows
        ]
        for metric in (
            "production",
            "throughput",
            "performance_percent",
            "defect_rate_percent",
            "downtime_minutes",
            "available_time_minutes",
            "machine_failure_count",
            "unplanned_downtime_events",
        )
    }
    if not source_flags["has_production"]:
        metrics["production"] = []
        metrics["throughput"] = []
        metrics["performance_percent"] = []
    if not source_flags["has_downtime"]:
        metrics["downtime_minutes"] = []
    if not source_flags["has_machine_state"]:
        metrics["machine_failure_count"] = []
    if not source_flags["has_unplanned_flag"]:
        metrics["unplanned_downtime_events"] = []
    metrics["defect_rate_percent"] = [
        row for row in metrics["defect_rate_percent"]
        if row["value"] is not None
    ]
    metrics["throughput"] = [
        row for row in metrics["throughput"] if row["value"] is not None
    ]
    metrics["performance_percent"] = [
        row for row in metrics["performance_percent"] if row["value"] is not None
    ]

    forecasts = {
        metric: _forecast_metric(
            metric,
            observations,
            horizon=horizon,
            coverage_percent=coverage_percent,
        )
        for metric, observations in metrics.items()
        if observations
    }
    material_forecasts: list[dict[str, Any]] = []
    material_quality_forecasts: list[dict[str, Any]] = []
    for key, observations in sorted(material_metrics.items()):
        if key.startswith(("loss::", "quality::")):
            continue
        material = observations[0]["material"]
        unit = observations[0]["unit"]
        material_key = f"{material}|{unit or 'unknown'}"
        forecast = _forecast_metric(
            f"material_consumption:{material}",
            observations,
            horizon=horizon,
            coverage_percent=coverage_percent,
        )
        forecast.update(
            {
                "material_key": material_key,
                "material": material,
                "unit": unit,
            }
        )
        material_forecasts.append(forecast)
        loss_observations = material_metrics.get(f"loss::{key}", [])
        if loss_observations:
            material_loss_forecast = _forecast_metric(
                f"material_loss:{material}",
                loss_observations,
                horizon=horizon,
                coverage_percent=coverage_percent,
            )
            material_loss_forecast.update(
                {
                    "material_key": material_key,
                    "material": material,
                    "unit": unit,
                }
            )
            forecasts.setdefault("material_loss", []).append(material_loss_forecast)
        quality_observations = material_metrics.get(f"quality::{key}", [])
        if quality_observations:
            material_quality_forecast = _forecast_metric(
                f"material_defect_rate:{material}",
                quality_observations,
                horizon=horizon,
                coverage_percent=coverage_percent,
            )
            material_quality_forecast.update(
                {
                    "material_key": material_key,
                    "material": material,
                    "unit": unit,
                    "threshold_percent": defect_threshold_percent,
                    "threshold_signals": [
                        {
                            **row,
                            "status": (
                                "threshold_exceedance_signal"
                                if row["value"] > defect_threshold_percent
                                else "below_threshold"
                            ),
                            "probability_claimed": False,
                        }
                        for row in material_quality_forecast["forecast"]
                    ],
                }
            )
            material_quality_forecasts.append(material_quality_forecast)

    if material_forecasts:
        forecasts["material_consumption"] = {
            "status": (
                "forecasted"
                if any(item["status"] == "forecasted" for item in material_forecasts)
                else "insufficient_data"
            ),
            "by_material": material_forecasts,
        }
    material_shortage_risk = _material_shortage_assessment(
        material_forecasts,
        material_stock_rows,
    )
    if source_flags["has_machine_state"]:
        failure_assessment = _machine_failure_assessment(
            metrics["machine_failure_count"]
        )
        failure_assessment["by_machine"] = _machine_failure_by_machine(dataset)
    else:
        failure_assessment = {
            "status": "insufficient_data",
            "observed_period_count": 0,
            "failure_period_count": 0,
            "empirical_failure_probability_percent": None,
            "interval_95_percent": None,
            "interpretation": "Machine Status/Downtime Type labels are required.",
            "by_machine": [],
        }

    quality_forecast = forecasts.get("defect_rate_percent")
    quality_risk = []
    if quality_forecast and quality_forecast["status"] == "forecasted":
        quality_risk = [
            {
                **row,
                "threshold_percent": defect_threshold_percent,
                "status": (
                    "threshold_exceedance_signal"
                    if row["value"] > defect_threshold_percent
                    else "below_threshold"
                ),
                "probability_claimed": False,
            }
            for row in quality_forecast["forecast"]
        ]
    completion_forecast = forecasts.get("performance_percent")
    production_plan_completion = {
        "status": (
            "forecasted"
            if completion_forecast
            and completion_forecast["status"] == "forecasted"
            else "insufficient_data"
        ),
        "threshold_percent": 100,
        "daily_assessment": [
            {
                **row,
                "status": (
                    "plan_shortfall_signal"
                    if row["value"] < 100
                    else "plan_attainment_signal"
                ),
                "probability_claimed": False,
            }
            for row in completion_forecast["forecast"]
        ]
        if completion_forecast
        and completion_forecast["status"] == "forecasted"
        else [],
        "interpretation": (
            "Forecast of historical daily production-to-plan ratio; not a "
            "schedule or order-completion guarantee."
        ),
    }
    scenarios = _scenario_simulation(
        forecasts,
        capacity_change_percent=capacity_change_percent,
        downtime_reduction_percent=downtime_reduction_percent,
        material_shortage_percent=material_shortage_percent,
        quality_improvement_percentage_points=(
            quality_improvement_percentage_points
        ),
    )

    history_adequate = (
        len(date_values) >= _MIN_OBSERVED_PERIODS
        and coverage_percent >= 50
    )
    limitations: list[str] = []
    if not date_values:
        limitations.append(
            "Không có timestamp hợp lệ; không thể chuẩn hóa chuỗi thời gian."
        )
    elif not history_adequate:
        limitations.append(
            f"Cần ít nhất {_MIN_OBSERVED_PERIODS} ngày có dữ liệu và độ phủ "
            "lịch sử từ 50% để chạy thống kê dự báo."
        )
    if not source_flags["has_production"]:
        limitations.append("Thiếu Production; dự báo sản lượng/throughput không khả dụng.")
    if not source_flags["has_downtime"]:
        limitations.append("Thiếu Downtime; không thể dự báo thời gian dừng máy.")
    if not source_flags["has_machine_state"]:
        limitations.append("Thiếu Machine Status/Downtime Type; đánh giá hỏng hóc chưa khả dụng.")
    if not material_forecasts:
        limitations.append("Thiếu Material và Actual Consumption để dự báo tiêu hao.")
    if not material_quality_forecasts:
        limitations.append(
            "Thiếu dữ liệu ghép cặp Material với Defects và Production/Inspected; "
            "dự báo rủi ro chất lượng theo vật liệu chưa khả dụng."
        )
    if not material_stock_rows:
        limitations.append("Thiếu Material Stock; không thể đánh giá tín hiệu thiếu vật liệu.")
    if not quality_forecast:
        limitations.append("Thiếu dữ liệu ghép cặp Defects và Production/Inspected.")
    if quality_forecast and quality_forecast["status"] != "forecasted":
        limitations.append("Lịch sử lỗi chưa đủ cho dự báo chất lượng.")
    if any(
        forecast.get("status") == "insufficient_data"
        for forecast in forecasts.values()
        if isinstance(forecast, dict)
    ):
        limitations.append(
            "Một hoặc nhiều chuỗi KPI không đủ mẫu cho backtest thời gian; "
            "engine không tự huấn luyện ML hay điền giá trị thiếu."
        )
    limitations.append(
        "Dự báo và kịch bản là sàng lọc thống kê; không chứng minh quan hệ nhân quả."
    )
    limitations.append(
        "Drift là so sánh phân phối gần đây với baseline trong file, không phải "
        "giám sát mô hình liên tục sau triển khai."
    )
    limitations.extend(dataset.data_quality.warnings)

    feature_rows_by_metric: dict[str, Any] = {}
    training_samples: dict[str, list[dict[str, Any]]] = {}
    for metric, observations in metrics.items():
        if not observations:
            continue
        clean = [item for item in observations if item["value"] is not None]
        rows = _build_feature_rows(clean)
        split = _split_ranges(rows)
        feature_rows_by_metric[metric] = split
        training_samples[metric] = [
            {
                "target_period": row["target_period"],
                "feature_source_end_period": row["feature_source_end_period"],
                "features": row["features"],
                "target": _rounded(row["target"]),
            }
            for row in rows[:2]
        ]

    for key, observations in material_metrics.items():
        if not observations:
            continue
        clean = [item for item in observations if item["value"] is not None]
        rows = _build_feature_rows(clean)
        if key.startswith("loss::"):
            metric = f"material_loss:{key.removeprefix('loss::')}"
        elif key.startswith("quality::"):
            metric = f"material_defect_rate:{key.removeprefix('quality::')}"
        else:
            metric = f"material_consumption:{key}"
        feature_rows_by_metric[metric] = _split_ranges(rows)
        training_samples[metric] = [
            {
                "target_period": row["target_period"],
                "feature_source_end_period": row["feature_source_end_period"],
                "features": row["features"],
                "target": _rounded(row["target"]),
            }
            for row in rows[:2]
        ]

    return {
        "schema_version": "1.0",
        "analysis_type": "predictive_intelligence",
        "forecast_horizon_periods": horizon,
        "data_quality": dataset.data_quality.model_dump(mode="json"),
        "predictive_data_foundation": {
            "status": "adequate_for_statistical_baseline" if history_adequate else "insufficient_data",
            "source_row_count": len(dataset.records),
            "timestamped_row_count": sum(
                record.timestamp is not None for record in dataset.records
            ),
            "observed_period_count": len(date_values),
            "calendar_span_days": calendar_days,
            "calendar_coverage_percent": _rounded(coverage_percent),
            "time_series": daily_rows,
            "features": [
                "lag_1_observed_period",
                "lag_7_observed_periods",
                "rolling_mean_7_observed_periods",
                "rolling_std_7_observed_periods",
                "days_since_previous_observation",
            ],
            "training_dataset": {
                "row_count_by_metric": {
                    metric: sum(split[name]["count"] for name in split)
                    for metric, split in feature_rows_by_metric.items()
                },
                "temporal_splits_by_metric": feature_rows_by_metric,
                "sample_rows_by_metric": training_samples,
                "leakage_controls": {
                    "features_use_only_prior_observations": True,
                    "splits_are_chronological_and_non_overlapping": True,
                    "validation_selects_baseline_method": True,
                    "test_set_excluded_from_method_selection": True,
                },
            },
        },
        "forecasts": forecasts,
        "production_plan_completion": production_plan_completion,
        "quality_risk": {
            "threshold_percent": defect_threshold_percent,
            "threshold_signals": quality_risk,
        },
        "material_quality_risk": {
            "threshold_percent": defect_threshold_percent,
            "by_material": material_quality_forecasts,
        },
        "machine_failure_assessment": failure_assessment,
        "material_shortage_risk": material_shortage_risk,
        "scenario_simulation": scenarios,
        "model_performance_monitoring": {
            metric: {
                "backtest": forecast.get("backtest"),
                "drift": forecast.get("drift"),
                "confidence": forecast.get("confidence_assessment"),
            }
            for metric, forecast in forecasts.items()
            if isinstance(forecast, dict) and "backtest" in forecast
        },
        "analysis_limitations": list(dict.fromkeys(limitations)),
    }
