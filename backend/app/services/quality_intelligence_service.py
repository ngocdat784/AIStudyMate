from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ai_audit_context import AIAuditContext
from app.services.gemini_retry import call_gemini_with_retry
from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    ManufacturingRecord,
    normalize_manufacturing_dataframe,
)


logger = logging.getLogger(__name__)
_MIN_TREND_BASELINE = 8
_MIN_STABILITY_SUBGROUPS = 20
_MIN_CAPABILITY_VALUES = 30
_QUALITY_ANSWER_TOPICS = {
    "top_defect",
    "highest_defect_process",
    "quality_trend",
    "risky_products",
    "statistical_control",
}
_XBAR_R_CONSTANTS = {
    2: (1.880, 0.000, 3.267, 1.128),
    3: (1.023, 0.000, 2.574, 1.693),
    4: (0.729, 0.000, 2.282, 2.059),
    5: (0.577, 0.000, 2.114, 2.326),
    6: (0.483, 0.000, 2.004, 2.534),
    7: (0.419, 0.076, 1.924, 2.704),
    8: (0.373, 0.136, 1.864, 2.847),
    9: (0.337, 0.184, 1.816, 2.970),
    10: (0.308, 0.223, 1.777, 3.078),
}


class QualityAIStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class QualityAIAnswer(QualityAIStatement):
    topic: Literal[
        "top_defect",
        "highest_defect_process",
        "quality_trend",
        "risky_products",
        "statistical_control",
    ]


class QualityAIInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: QualityAIStatement
    answers: list[QualityAIAnswer] = Field(max_length=5)
    root_cause_hypotheses: list[QualityAIStatement] = Field(max_length=10)
    recommendations: list[QualityAIStatement] = Field(max_length=10)
    limitations: list[str] = Field(max_length=10)


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _add_evidence(
    evidence: list[dict[str, Any]],
    *,
    metric: str,
    value: Any,
    unit: str,
    method: str,
    record_count: int,
    dimensions: dict[str, str] | None = None,
    period: str | None = None,
) -> str:
    evidence_id = f"E{len(evidence) + 1:04d}"
    item: dict[str, Any] = {
        "id": evidence_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "method": method,
        "record_count": record_count,
    }
    if dimensions:
        item["dimensions"] = dimensions
    if period:
        item["period"] = period
    evidence.append(item)
    return evidence_id


def _metric(
    value: float | None,
    unit: str,
    basis: str,
    record_count: int,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    return {
        "value": _rounded(value),
        "unit": unit,
        "basis": basis,
        "record_count": record_count,
        "evidence_id": evidence_id,
    }


def _defect_category(record: ManufacturingRecord) -> str | None:
    category = (record.defect_type or record.defect or "").strip()
    return category or None


def _defect_events(record: ManufacturingRecord) -> float | None:
    if record.defects is not None:
        return float(record.defects)
    if record.defective_units is not None:
        return float(record.defective_units)
    if _defect_category(record):
        return 1.0
    return None


def _complete_rate(
    records: list[ManufacturingRecord],
    *,
    numerator_field: Literal[
        "defective_units",
        "defects",
        "first_pass_passed",
        "reworked_units",
        "scrapped_units",
    ],
    denominator_field: Literal[
        "inspected_quantity",
        "first_pass_inspected",
    ] = "inspected_quantity",
    require_complete_inspection_coverage: bool = True,
) -> tuple[float | None, int, str]:
    denominator_records = [
        record
        for record in records
        if getattr(record, denominator_field) is not None
    ]
    if not denominator_records:
        return None, 0, f"missing_{denominator_field}"
    if require_complete_inspection_coverage and any(
        record.inspected_quantity is None for record in records
    ):
        return None, len(denominator_records), "incomplete_inspection_coverage"
    if any(
        getattr(record, numerator_field) is None
        for record in denominator_records
    ):
        return None, len(denominator_records), f"incomplete_{numerator_field}"

    denominator = sum(
        float(getattr(record, denominator_field) or 0)
        for record in denominator_records
    )
    numerator = sum(
        float(getattr(record, numerator_field) or 0)
        for record in denominator_records
    )
    if denominator <= 0:
        return None, len(denominator_records), "zero_denominator"
    for record in denominator_records:
        numerator_value = float(getattr(record, numerator_field) or 0)
        denominator_value = float(getattr(record, denominator_field) or 0)
        if numerator_field in {
            "defective_units",
            "first_pass_passed",
            "reworked_units",
            "scrapped_units",
        } and numerator_value > denominator_value:
            return None, len(denominator_records), "numerator_exceeds_denominator"
    return numerator / denominator * 100, len(denominator_records), (
        f"{numerator_field}_divided_by_{denominator_field}"
    )


def _defect_rate(
    records: list[ManufacturingRecord],
) -> tuple[float | None, int, str]:
    inspected_records = [
        record for record in records if record.inspected_quantity is not None
    ]
    if not inspected_records:
        return None, 0, "missing_inspected_quantity"
    has_defective_unit_counts = any(
        record.defective_units is not None for record in inspected_records
    )
    if has_defective_unit_counts and any(
        record.defective_units is None for record in inspected_records
    ):
        return None, len(inspected_records), "incomplete_defective_units_coverage"
    if not has_defective_unit_counts:
        if any(
            record.defects is None
            and not _defect_category(record)
            for record in inspected_records
        ):
            return None, len(inspected_records), "incomplete_defect_count_coverage"
        numerator = sum(
            float(_defect_events(record) or 0)
            for record in inspected_records
        )
        denominator = sum(
            float(record.inspected_quantity or 0)
            for record in inspected_records
        )
        if denominator <= 0:
            return None, len(inspected_records), "zero_inspected_quantity"
        return (
            numerator / denominator * 100,
            len(inspected_records),
            "defect_events_per_inspected_item",
        )
    else:
        basis = "defective_units_divided_by_inspected_quantity"

    denominator = sum(
        float(record.inspected_quantity or 0)
        for record in inspected_records
    )
    numerator = sum(
        float(record.defective_units or 0)
        for record in inspected_records
    )
    if denominator <= 0:
        return None, len(inspected_records), "zero_inspected_quantity"
    if any(
        float(record.defective_units or 0)
        > float(record.inspected_quantity or 0)
        for record in inspected_records
    ):
        return None, len(inspected_records), "defective_units_exceed_inspected"
    return numerator / denominator * 100, len(inspected_records), basis


def _first_pass_rate(
    records: list[ManufacturingRecord],
) -> tuple[float | None, int, str]:
    process_records = [
        record for record in records if record.process_sequence is not None
    ]
    if process_records:
        if len(process_records) != len(records):
            return None, len(process_records), "incomplete_process_sequence_coverage"
        final_sequence = max(
            record.process_sequence or 0 for record in process_records
        )
        records = [
            record
            for record in process_records
            if record.process_sequence == final_sequence
        ]
        if any(record.first_pass_inspected is None for record in records):
            return None, len(records), "incomplete_final_process_inspection_coverage"
    inspected_records = [
        record
        for record in records
        if record.first_pass_inspected is not None
    ]
    if not inspected_records:
        return None, 0, "missing_first_pass_inspected"
    if any(
        record.first_pass_passed is None
        for record in inspected_records
    ):
        return None, len(inspected_records), "incomplete_first_pass_coverage"
    denominator = sum(
        float(record.first_pass_inspected or 0)
        for record in inspected_records
    )
    passed = sum(
        float(record.first_pass_passed or 0)
        for record in inspected_records
    )
    if denominator <= 0:
        return None, len(inspected_records), "zero_first_pass_inspected"
    if any(
        float(record.first_pass_passed or 0)
        > float(record.first_pass_inspected or 0)
        for record in inspected_records
    ):
        return None, len(inspected_records), "first_pass_passed_exceeds_inspected"
    return passed / denominator * 100, len(inspected_records), (
        "first_pass_passed_divided_by_first_pass_inspected"
    )


def _rolled_throughput_yield(
    records: list[ManufacturingRecord],
) -> tuple[float | None, str]:
    routed = [
        record
        for record in records
        if record.process_sequence is not None
        and record.first_pass_inspected is not None
        and record.first_pass_passed is not None
    ]
    if len(routed) != len(records) or not routed:
        return None, "requires_process_sequence_and_first_pass_counts"

    product_groups: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in routed:
        product_groups[record.product or "Unspecified product"].append(record)

    product_results: list[tuple[tuple[tuple[int, str], ...], float, float]] = []
    for product_records in product_groups.values():
        stages: dict[int, list[ManufacturingRecord]] = defaultdict(list)
        for record in product_records:
            stages[record.process_sequence or 0].append(record)
        if len(stages) < 2:
            return None, "requires_at_least_two_ordered_processes"
        signature: list[tuple[int, str]] = []
        yields: list[float] = []
        first_stage_inspected = 0.0
        for sequence, stage_records in sorted(stages.items()):
            processes = {record.process or "" for record in stage_records}
            if len(processes) != 1:
                return None, "process_sequence_maps_to_multiple_processes"
            inspected = sum(
                float(record.first_pass_inspected or 0)
                for record in stage_records
            )
            passed = sum(
                float(record.first_pass_passed or 0)
                for record in stage_records
            )
            if inspected <= 0 or passed > inspected:
                return None, "invalid_process_first_pass_counts"
            if not first_stage_inspected:
                first_stage_inspected = inspected
            signature.append((sequence, next(iter(processes))))
            yields.append(passed / inspected)
        product_results.append(
            (tuple(signature), math.prod(yields), first_stage_inspected)
        )

    signatures = {item[0] for item in product_results}
    if len(signatures) != 1:
        return None, "products_have_different_process_routes"
    total_weight = sum(item[2] for item in product_results)
    if total_weight <= 0:
        return None, "zero_first_process_inspected"
    weighted_yield = sum(
        result * weight for _, result, weight in product_results
    ) / total_weight
    return weighted_yield * 100, "product_of_ordered_process_first_pass_yields"


def _pareto(
    records: list[ManufacturingRecord],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[str, float] = defaultdict(float)
    first_seen: dict[str, str | None] = {}
    source_counts: dict[str, int] = defaultdict(int)
    for record in records:
        category = _defect_category(record)
        count = _defect_events(record)
        if category is None:
            if count is None or count <= 0:
                continue
            category = "Unclassified"
        elif count is None:
            count = 1.0
        if count <= 0:
            continue
        counts[category] += count
        source_counts[category] += 1
        period = record.timestamp.isoformat() if record.timestamp else None
        if category not in first_seen or (
            period is not None
            and (
                first_seen[category] is None
                or period < str(first_seen[category])
            )
        ):
            first_seen[category] = period

    total = sum(counts.values())
    cumulative = 0.0
    result: list[dict[str, Any]] = []
    for category, count in sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0].casefold()),
    ):
        share = count / total * 100 if total else 0.0
        cumulative += share
        evidence_id = _add_evidence(
            evidence,
            metric="defect_category_count",
            value={"category": category, "count": _rounded(count)},
            unit="defects",
            method="sum_explicit_defects_or_one_per_classified_row",
            record_count=source_counts[category],
            dimensions={"defect_category": category},
        )
        result.append(
            {
                "category": category,
                "defects": _rounded(count),
                "share_percent": _rounded(share),
                "cumulative_share_percent": _rounded(cumulative),
                "dominant": cumulative - share < 80,
                "first_seen": first_seen.get(category),
                "evidence_id": evidence_id,
            }
        )
    return result


def _breakdown(
    records: list[ManufacturingRecord],
    dimension: Literal["product", "process", "line", "shift"],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        label = getattr(record, dimension)
        if label:
            groups[label].append(record)

    result: list[dict[str, Any]] = []
    for label, group in groups.items():
        inspected_records = [
            record for record in group if record.inspected_quantity is not None
        ]
        inspected = (
            sum(float(record.inspected_quantity or 0) for record in inspected_records)
            if inspected_records
            else None
        )
        defects = sum(
            float(count or 0)
            for count in (_defect_events(record) for record in group)
            if count is not None
        )
        defect_rate, rate_count, rate_basis = _defect_rate(group)
        evidence_id = _add_evidence(
            evidence,
            metric=f"quality_by_{dimension}",
            value={
                "defects": _rounded(defects),
                "inspected": _rounded(inspected),
                "defect_rate_percent": _rounded(defect_rate),
            },
            unit="mixed",
            method=rate_basis,
            record_count=len(group),
            dimensions={dimension: label},
        )
        result.append(
            {
                dimension: label,
                "inspected": _rounded(inspected),
                "defects": _rounded(defects),
                "defect_rate_percent": _rounded(defect_rate),
                "rate_record_count": rate_count,
                "record_count": len(group),
                "evidence_id": evidence_id,
            }
        )
    return sorted(
        result,
        key=lambda row: (
            row["defect_rate_percent"] is None,
            -(row["defect_rate_percent"] or 0),
            row[dimension].casefold(),
        ),
    )


def _periods(
    records: list[ManufacturingRecord],
    granularity: Literal["day", "week", "month"],
    *,
    source_timezone: str,
) -> list[dict[str, Any]]:
    timezone = ZoneInfo(source_timezone)
    grouped: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if record.timestamp is None:
            continue
        day = record.timestamp.astimezone(timezone).date()
        if granularity == "day":
            key = day.isoformat()
        elif granularity == "week":
            key = (day - timedelta(days=day.weekday())).isoformat()
        else:
            key = f"{day.year:04d}-{day.month:02d}"
        grouped[key].append(record)

    result: list[dict[str, Any]] = []
    for period, group in sorted(grouped.items()):
        inspected = [
            record for record in group if record.inspected_quantity is not None
        ]
        inspected_total = (
            sum(float(record.inspected_quantity or 0) for record in inspected)
            if inspected
            else None
        )
        defect_rate, rate_count, rate_basis = _defect_rate(group)
        fpy, _, fpy_basis = _first_pass_rate(group)
        result.append(
            {
                "period": period,
                "inspected": _rounded(inspected_total),
                "defects": _rounded(
                    sum(
                        float(count or 0)
                        for count in (
                            _defect_events(record) for record in group
                        )
                        if count is not None
                    )
                ),
                "defect_rate_percent": _rounded(defect_rate),
                "first_pass_yield_percent": _rounded(fpy),
                "record_count": len(group),
                "rate_record_count": rate_count,
                "rate_basis": rate_basis,
                "first_pass_basis": fpy_basis,
            }
        )
    return result


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )


def _quality_trend(
    daily: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    periods = [
        item
        for item in daily
        if item["defect_rate_percent"] is not None
    ]
    if len(periods) < 4:
        return {
            "direction": "insufficient_data",
            "change_percentage_points": None,
            "evidence_id": None,
            "method": "requires_four_valid_daily_defect_rates",
        }
    midpoint = len(periods) // 2
    earlier = statistics.fmean(
        float(item["defect_rate_percent"]) for item in periods[:midpoint]
    )
    recent = statistics.fmean(
        float(item["defect_rate_percent"]) for item in periods[midpoint:]
    )
    delta = recent - earlier
    direction = (
        "declining_quality"
        if delta >= 1
        else "improving_quality"
        if delta <= -1
        else "stable"
    )
    evidence_id = _add_evidence(
        evidence,
        metric="quality_trend",
        value={
            "earlier_average_defect_rate_percent": _rounded(earlier),
            "recent_average_defect_rate_percent": _rounded(recent),
            "change_percentage_points": _rounded(delta),
        },
        unit="percentage_points",
        method="comparison_of_first_and_second_daily_period_halves",
        record_count=len(periods),
    )
    return {
        "direction": direction,
        "change_percentage_points": _rounded(delta),
        "earlier_average_defect_rate_percent": _rounded(earlier),
        "recent_average_defect_rate_percent": _rounded(recent),
        "evidence_id": evidence_id,
        "method": "comparison_of_first_and_second_daily_period_halves",
    }


def _quality_anomalies(
    daily: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    valid = [
        item
        for item in daily
        if item["defect_rate_percent"] is not None
    ]
    if len(valid) < _MIN_TREND_BASELINE + 1:
        return []
    baseline_items = valid[-(_MIN_TREND_BASELINE + 1) : -1]
    baseline = [
        float(item["defect_rate_percent"]) for item in baseline_items
    ]
    observed_item = valid[-1]
    observed = float(observed_item["defect_rate_percent"])
    q1 = _quantile(baseline, 0.25)
    q3 = _quantile(baseline, 0.75)
    upper = q3 + 1.5 * (q3 - q1)
    if observed <= upper:
        return []
    median = statistics.median(baseline)
    increase = (
        (observed - median) / median * 100 if median > 0 else 100.0
    )
    evidence_id = _add_evidence(
        evidence,
        metric="quality_rate_increase",
        value={
            "observed_percent": _rounded(observed),
            "baseline_median_percent": _rounded(median),
            "upper_iqr_bound_percent": _rounded(upper),
            "baseline_periods": len(baseline),
        },
        unit="percent",
        method="prior_eight_periods_iqr_1_5",
        record_count=len(baseline) + 1,
        period=observed_item["period"],
    )
    return [
        {
            "type": "defect_rate_increase",
            "period": observed_item["period"],
            "observed_percent": _rounded(observed),
            "baseline_median_percent": _rounded(median),
            "upper_bound_percent": _rounded(upper),
            "increase_percent": _rounded(increase),
            "severity": (
                "high" if increase >= 20 else "medium" if increase >= 10 else "low"
            ),
            "method": "prior_eight_periods_iqr_1_5",
            "evidence_ids": [evidence_id],
        }
    ]


def _scope_key(record: ManufacturingRecord) -> tuple[str, str, str]:
    return (
        record.line or "All Lines",
        record.process or "All Processes",
        record.product or "All Products",
    )


def _scope_label(scope: tuple[str, ...]) -> str:
    return " · ".join(scope)


def _sample_label(record: ManufacturingRecord) -> str:
    if record.subgroup_id:
        return record.subgroup_id
    if record.batch:
        return record.batch
    if record.timestamp:
        return record.timestamp.isoformat()
    return f"row-{record.source_row}"


def _chart_base(
    *,
    chart_type: str,
    scope: tuple[str, ...],
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "chart_type": chart_type,
        "scope": _scope_label(scope),
        "status": "insufficient_data",
        "stability": "insufficient_data",
        "center_line": None,
        "lower_control_limit": None,
        "upper_control_limit": None,
        "points": [],
        "out_of_control_points": [],
        "method": "three_sigma_control_limits",
    }
    if reason:
        result["reason"] = reason
    return result


def _build_xbar_r(
    records: list[ManufacturingRecord],
    scope: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any], float | None, int | None, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.measurement_value is None:
            continue
        subgroup = record.subgroup_id or record.batch
        if subgroup:
            groups[subgroup].append(float(record.measurement_value))

    xbar = _chart_base(
        chart_type="x_bar",
        scope=scope,
        reason="Requires Subgroup ID or Batch and equal subgroup sizes from 2 to 10.",
    )
    r_chart = _chart_base(
        chart_type="r",
        scope=scope,
        reason="Requires Subgroup ID or Batch and equal subgroup sizes from 2 to 10.",
    )
    sizes = {len(values) for values in groups.values()}
    if len(groups) < 2 or len(sizes) != 1:
        return xbar, r_chart, None, None, []
    subgroup_size = next(iter(sizes))
    if subgroup_size not in _XBAR_R_CONSTANTS:
        return xbar, r_chart, None, subgroup_size, []

    a2, d3, d4, _ = _XBAR_R_CONSTANTS[subgroup_size]
    subgroup_means = {
        name: statistics.fmean(values) for name, values in groups.items()
    }
    subgroup_ranges = {
        name: max(values) - min(values) for name, values in groups.items()
    }
    grand_mean = statistics.fmean(subgroup_means.values())
    average_range = statistics.fmean(subgroup_ranges.values())
    x_lcl = grand_mean - a2 * average_range
    x_ucl = grand_mean + a2 * average_range
    r_lcl = d3 * average_range
    r_ucl = d4 * average_range
    x_points: list[dict[str, Any]] = []
    r_points: list[dict[str, Any]] = []
    for name in sorted(groups):
        x_value = subgroup_means[name]
        r_value = subgroup_ranges[name]
        x_out = x_value < x_lcl or x_value > x_ucl
        r_out = r_value < r_lcl or r_value > r_ucl
        x_points.append(
            {
                "sample": name,
                "value": _rounded(x_value),
                "out_of_control": x_out,
            }
        )
        r_points.append(
            {
                "sample": name,
                "value": _rounded(r_value),
                "out_of_control": r_out,
            }
        )
    x_stability = (
        "out_of_control"
        if any(point["out_of_control"] for point in x_points)
        else "stable"
        if len(groups) >= _MIN_STABILITY_SUBGROUPS
        else "provisional"
    )
    r_stability = (
        "out_of_control"
        if any(point["out_of_control"] for point in r_points)
        else "stable"
        if len(groups) >= _MIN_STABILITY_SUBGROUPS
        else "provisional"
    )
    for chart, center, lower, upper, points, stability in (
        (xbar, grand_mean, x_lcl, x_ucl, x_points, x_stability),
        (r_chart, average_range, r_lcl, r_ucl, r_points, r_stability),
    ):
        chart.update(
            {
                "status": "available",
                "stability": stability,
                "center_line": _rounded(center),
                "lower_control_limit": _rounded(lower),
                "upper_control_limit": _rounded(upper),
                "subgroup_size": subgroup_size,
                "subgroup_count": len(groups),
                "points": points,
                "out_of_control_points": [
                    point for point in points if point["out_of_control"]
                ],
                "reason": (
                    None
                    if len(groups) >= _MIN_STABILITY_SUBGROUPS
                    else f"Provisional: requires at least {_MIN_STABILITY_SUBGROUPS} subgroups to assess stability."
                ),
            }
        )
    return xbar, r_chart, average_range, subgroup_size, [
        float(value)
        for values in groups.values()
        for value in values
    ]


def _build_count_chart(
    records: list[ManufacturingRecord],
    scope: tuple[str, ...],
    chart_type: Literal["p", "c", "u"],
) -> dict[str, Any]:
    chart = _chart_base(
        chart_type=chart_type,
        scope=scope,
        reason=None,
    )
    samples: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}
    for record in records:
        inspected = record.inspected_quantity
        if inspected is None or inspected <= 0:
            continue
        if chart_type == "p":
            if record.defective_units is None:
                continue
            count = float(record.defective_units)
            if count > inspected:
                continue
        else:
            count = _defect_events(record)
            if count is None:
                continue
        sample_id = record.subgroup_id or record.batch
        sample_key = (
            ("sample", sample_id)
            if sample_id
            else ("row", str(record.source_row))
        )
        sample = samples.setdefault(
            sample_key,
            {
                "record": record,
                "count": 0.0,
                "inspected": 0.0,
            },
        )
        sample["count"] += count
        sample["inspected"] += float(inspected)

    observations = list(samples.values())
    if len(observations) < 2:
        chart["reason"] = (
            "Requires at least two samples with inspected quantity and "
            + ("defective units." if chart_type == "p" else "defect counts.")
        )
        return chart
    if chart_type == "c" and len(
        {sample_size for _, _, sample_size in observations}
    ) != 1:
        chart["reason"] = "C chart requires a constant sample size; use U chart for varying sizes."
        return chart

    total_count = sum(sample["count"] for sample in observations)
    total_inspected = sum(sample["inspected"] for sample in observations)
    center = (
        total_count / len(observations)
        if chart_type == "c"
        else total_count / total_inspected
    )
    points: list[dict[str, Any]] = []
    for sample in observations:
        record = sample["record"]
        count = sample["count"]
        sample_size = sample["inspected"]
        if chart_type == "p":
            value = count / sample_size
            sigma = math.sqrt(center * (1 - center) / sample_size)
            lower = max(0.0, center - 3 * sigma)
            upper = min(1.0, center + 3 * sigma)
        elif chart_type == "c":
            value = count
            lower = max(0.0, center - 3 * math.sqrt(center))
            upper = center + 3 * math.sqrt(center)
        else:
            value = count / sample_size
            sigma = math.sqrt(center / sample_size)
            lower = max(0.0, center - 3 * sigma)
            upper = center + 3 * sigma
        points.append(
            {
                "sample": _sample_label(record),
                "period": (
                    record.timestamp.isoformat()
                    if record.timestamp is not None
                    else None
                ),
                "value": _rounded(value),
                "sample_size": _rounded(sample_size),
                "lower_control_limit": _rounded(lower),
                "upper_control_limit": _rounded(upper),
                "out_of_control": value < lower or value > upper,
            }
        )
    out_of_control = [
        point for point in points if point["out_of_control"]
    ]
    stability = (
        "out_of_control"
        if out_of_control and len(points) >= _MIN_STABILITY_SUBGROUPS
        else "stable"
        if not out_of_control and len(points) >= _MIN_STABILITY_SUBGROUPS
        else "provisional"
    )
    chart.update(
        {
            "status": "available",
            "stability": stability,
            "center_line": _rounded(center),
            "points": points,
            "out_of_control_points": out_of_control,
            "reason": (
                None
                if len(points) >= _MIN_STABILITY_SUBGROUPS
                else f"Provisional: requires at least {_MIN_STABILITY_SUBGROUPS} samples to assess stability."
            ),
        }
    )
    return chart


def _jarque_bera_p_value(values: list[float]) -> float | None:
    if len(values) < _MIN_CAPABILITY_VALUES:
        return None
    mean = statistics.fmean(values)
    second = statistics.fmean((value - mean) ** 2 for value in values)
    if second <= 0:
        return 0.0
    third = statistics.fmean((value - mean) ** 3 for value in values)
    fourth = statistics.fmean((value - mean) ** 4 for value in values)
    skewness = third / second**1.5
    excess_kurtosis = fourth / second**2 - 3
    statistic = len(values) / 6 * (
        skewness**2 + excess_kurtosis**2 / 4
    )
    return math.exp(-statistic / 2)


def _process_capability(
    records: list[ManufacturingRecord],
    *,
    average_range: float | None,
    subgroup_size: int | None,
    stability: str,
    measurement_unit: str,
) -> dict[str, Any]:
    values = [
        float(record.measurement_value)
        for record in records
        if record.measurement_value is not None
    ]
    limits = {
        (record.lower_spec_limit, record.upper_spec_limit)
        for record in records
        if record.lower_spec_limit is not None
        and record.upper_spec_limit is not None
    }
    result: dict[str, Any] = {
        "status": "insufficient_data",
        "cp": None,
        "cpk": None,
        "unit": "ratio",
        "sample_count": len(values),
        "measurement_unit": measurement_unit,
        "normality_p_value": None,
        "method": "within_sigma_from_rbar_divided_by_d2",
    }
    if len(limits) != 1:
        result["reason"] = "Requires one consistent Lower Spec Limit and Upper Spec Limit."
        return result
    lsl, usl = next(iter(limits))
    if lsl is None or usl is None or usl <= lsl:
        result["reason"] = "Upper Spec Limit must be greater than Lower Spec Limit."
        return result
    if len(values) < _MIN_CAPABILITY_VALUES:
        result["reason"] = f"Requires at least {_MIN_CAPABILITY_VALUES} measurements."
        return result
    if stability != "stable":
        result["reason"] = "Capability indices require a stable X-bar/R process."
        return result
    if average_range is None or subgroup_size not in _XBAR_R_CONSTANTS:
        result["reason"] = "Capability indices require valid equal-sized subgroups."
        return result
    normality_p = _jarque_bera_p_value(values)
    result["normality_p_value"] = _rounded(normality_p)
    if normality_p is None or normality_p < 0.05:
        result["reason"] = "Normality screen did not meet p >= 0.05."
        return result

    d2 = _XBAR_R_CONSTANTS[subgroup_size][3]
    within_sigma = average_range / d2
    if within_sigma <= 0:
        result["reason"] = "Within-subgroup standard deviation is zero."
        return result
    mean = statistics.fmean(values)
    cp = (usl - lsl) / (6 * within_sigma)
    cpk = min(usl - mean, mean - lsl) / (3 * within_sigma)
    result.update(
        {
            "status": "available",
            "cp": _rounded(cp),
            "cpk": _rounded(cpk),
            "mean": _rounded(mean),
            "lower_spec_limit": _rounded(lsl),
            "upper_spec_limit": _rounded(usl),
            "within_subgroup_sigma": _rounded(within_sigma),
            "normality_method": "Jarque-Bera approximation; p >= 0.05 screen",
            "reason": None,
        }
    )
    return result


def _spc(
    records: list[ManufacturingRecord],
    evidence: list[dict[str, Any]],
    *,
    measurement_unit: str,
) -> dict[str, Any]:
    measurement_groups: dict[tuple[str, ...], list[ManufacturingRecord]] = defaultdict(list)
    count_groups: dict[tuple[str, ...], list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if record.measurement_value is not None:
            measurement_groups[
                (
                    record.measurement_name or "Unspecified characteristic",
                    *_scope_key(record),
                )
            ].append(record)
        if (
            record.inspected_quantity is not None
            or record.defects is not None
            or record.defective_units is not None
        ):
            count_groups[_scope_key(record)].append(record)

    xbar_r_charts: list[dict[str, Any]] = []
    capability: list[dict[str, Any]] = []
    for scope, group in sorted(measurement_groups.items()):
        xbar, r_chart, average_range, subgroup_size, _ = _build_xbar_r(
            group,
            scope,
        )
        for chart in (xbar, r_chart):
            if chart["status"] == "available":
                chart["evidence_id"] = _add_evidence(
                    evidence,
                    metric=f"spc_{chart['chart_type']}",
                    value={
                        "center_line": chart["center_line"],
                        "lower_control_limit": chart["lower_control_limit"],
                        "upper_control_limit": chart["upper_control_limit"],
                        "out_of_control_points": chart["out_of_control_points"],
                    },
                    unit=measurement_unit,
                    method="three_sigma_xbar_r_constants",
                    record_count=chart["subgroup_count"],
                    dimensions={"scope": chart["scope"]},
                )
        xbar_r_charts.append(
            {
                "characteristic": scope[0],
                "scope": _scope_label(scope[1:]),
                "x_bar": xbar,
                "r": r_chart,
            }
        )
        capability_result = _process_capability(
            group,
            average_range=average_range,
            subgroup_size=subgroup_size,
            stability=xbar["stability"]
            if xbar["stability"] == r_chart["stability"]
            else "out_of_control",
            measurement_unit=measurement_unit,
        )
        if capability_result["status"] == "available":
            capability_result["evidence_id"] = _add_evidence(
                evidence,
                metric="process_capability",
                value={
                    "cp": capability_result["cp"],
                    "cpk": capability_result["cpk"],
                    "normality_p_value": capability_result["normality_p_value"],
                },
                unit="ratio",
                method=capability_result["method"],
                record_count=capability_result["sample_count"],
                dimensions={"scope": _scope_label(scope)},
            )
        capability_result.update(
            {
                "characteristic": scope[0],
                "scope": _scope_label(scope[1:]),
            }
        )
        capability.append(capability_result)

    count_charts: dict[str, list[dict[str, Any]]] = {
        chart_type: [] for chart_type in ("p", "c", "u")
    }
    for scope, group in sorted(count_groups.items()):
        for chart_type in ("p", "c", "u"):
            chart = _build_count_chart(group, scope, chart_type)  # type: ignore[arg-type]
            if chart["status"] == "available":
                chart["evidence_id"] = _add_evidence(
                    evidence,
                    metric=f"spc_{chart_type}_chart",
                    value={
                        "center_line": chart["center_line"],
                        "out_of_control_points": chart["out_of_control_points"],
                    },
                    unit=(
                        "proportion"
                        if chart_type == "p"
                        else "defects"
                        if chart_type == "c"
                        else "defects/inspected"
                    ),
                    method=f"three_sigma_{chart_type}_chart",
                    record_count=len(chart["points"]),
                    dimensions={"scope": chart["scope"]},
                )
            count_charts[chart_type].append(chart)

    all_charts = [
        chart
        for item in xbar_r_charts
        for chart in (item["x_bar"], item["r"])
    ] + [
        chart
        for chart_type in ("p", "c", "u")
        for chart in count_charts[chart_type]
    ]
    evaluated = [
        chart["stability"]
        for chart in all_charts
        if chart["status"] == "available"
    ]
    if any(status == "out_of_control" for status in evaluated):
        stability = "out_of_control"
    elif evaluated and all(status == "stable" for status in evaluated):
        stability = "stable"
    else:
        stability = "insufficient_data"

    anomalies = [
        {
            "type": f"{chart['chart_type']}_out_of_control",
            "scope": chart["scope"],
            "sample": point["sample"],
            "period": point.get("period"),
            "value": point["value"],
            "lower_control_limit": point.get(
                "lower_control_limit",
                chart["lower_control_limit"],
            ),
            "upper_control_limit": point.get(
                "upper_control_limit",
                chart["upper_control_limit"],
            ),
            "evidence_ids": (
                [chart["evidence_id"]]
                if chart.get("evidence_id")
                else []
            ),
        }
        for chart in all_charts
        for point in chart["out_of_control_points"]
    ]
    return {
        "process_stability": {
            "status": stability,
            "evaluated_chart_count": len(evaluated),
            "minimum_stability_subgroups": _MIN_STABILITY_SUBGROUPS,
            "rule": "three_sigma_limits_only",
        },
        "charts": {
            "x_bar_r": xbar_r_charts,
            "p": count_charts["p"],
            "c": count_charts["c"],
            "u": count_charts["u"],
        },
        "capability": capability,
        "anomalies": anomalies,
    }


def _normalize_status(status: str | None) -> str:
    value = (status or "").strip().casefold()
    if value in {"pass", "passed", "accept", "accepted", "ok", "good", "đạt"}:
        return "pass"
    if value in {"fail", "failed", "reject", "rejected", "ng", "not ok", "lỗi"}:
        return "fail"
    if value in {"rework", "reworked", "repair", "đang sửa"}:
        return "rework"
    if value in {"scrap", "scrapped", "discard", "loại bỏ"}:
        return "scrap"
    return "unclassified"


def _ai_validation(
    interpretation: QualityAIInterpretation,
    evidence_ids: set[str],
) -> None:
    topics = {answer.topic for answer in interpretation.answers}
    if topics != _QUALITY_ANSWER_TOPICS or len(interpretation.answers) != len(
        _QUALITY_ANSWER_TOPICS
    ):
        raise ValueError("Quality AI must answer every required question.")
    statements = [
        interpretation.executive_summary,
        *interpretation.answers,
        *interpretation.root_cause_hypotheses,
        *interpretation.recommendations,
    ]
    for statement in statements:
        if not set(statement.evidence_ids).issubset(evidence_ids):
            raise ValueError("Quality AI referenced an unknown evidence ID.")
        if any(character.isdigit() for character in statement.text):
            raise ValueError("Quality AI must link numeric claims by evidence ID.")
    if any(
        any(character.isdigit() for character in limitation)
        for limitation in interpretation.limitations
    ):
        raise ValueError("Quality AI limitation contains an unsupported number.")


def _interpret_with_gemini(
    quality: dict[str, Any],
) -> tuple[QualityAIInterpretation | None, str, str | None]:
    if not settings.GEMINI_API_KEY:
        return (
            None,
            "not_configured",
            "Gemini chưa được cấu hình; kết quả chất lượng tính bằng Python vẫn khả dụng.",
        )
    evidence_ids = {item["id"] for item in quality["evidence_references"]}
    if not evidence_ids:
        return None, "insufficient_evidence", "Không có evidence chất lượng."

    from app.services.hardware_ai_service import (
        GEMINI_MODEL,
        _gemini_response_schema,
        get_client,
    )

    context = {
        "questions": sorted(_QUALITY_ANSWER_TOPICS),
        "kpi_results": quality["kpi_results"],
        "defect_pareto": quality["defect_pareto"],
        "quality_by_product": quality["quality_by_product"],
        "quality_by_process": quality["quality_by_process"],
        "quality_trend": quality["quality_trend"],
        "spc": quality["spc"],
        "quality_anomalies": quality["quality_anomalies"],
        "evidence_references": quality["evidence_references"],
        "analysis_limitations": quality["analysis_limitations"],
    }
    prompt = (
        "Trả lời bằng tiếng Việt cho người quản lý chất lượng. Chỉ diễn giải "
        "kết quả Python và evidence được cung cấp; không tự tính số, tạo "
        "benchmark hoặc suy diễn nguyên nhân. Trả đủ đúng năm chủ đề "
        "top_defect, highest_defect_process, quality_trend, risky_products, "
        "statistical_control. Mọi statement phải dẫn evidence ID; không viết "
        "chữ số trong nội dung. Root cause chỉ là giả thuyết, khuyến nghị phải "
        "có thể kiểm chứng. Nêu rõ khi SPC hoặc KPI chưa đủ dữ liệu. JSON:\n"
        + json.dumps(context, ensure_ascii=False, allow_nan=False)
    )
    audit = AIAuditContext(
        analysis_type="quality_intelligence",
        model_name=GEMINI_MODEL,
        prompt_version="quality_intelligence_v1",
        input_data=context,
        prompt_text=prompt,
    )
    try:
        response = call_gemini_with_retry(
            lambda: get_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": _gemini_response_schema(
                        QualityAIInterpretation
                    ),
                    "temperature": 0.2,
                },
            )
        )
    except Exception as error:
        audit.fail(error)
        logger.exception("Quality intelligence Gemini request failed")
        return (
            None,
            "error",
            "Gemini không khả dụng; giữ nguyên kết quả chất lượng tính bằng Python.",
        )

    audit.set_usage_from_response(response)
    raw_response = getattr(response, "text", None)
    audit.set_raw_response(raw_response)
    try:
        if not raw_response or not raw_response.strip():
            raise ValueError("Gemini không trả về quality interpretation.")
        interpretation = QualityAIInterpretation.model_validate_json(
            raw_response
        )
        _ai_validation(interpretation, evidence_ids)
    except Exception as error:
        audit.fail(error, validation_failed=True)
        logger.warning(
            "Quality intelligence response failed validation: %s",
            type(error).__name__,
        )
        return (
            None,
            "validation_failed",
            "Diễn giải Gemini không đạt kiểm định evidence; kết quả Python vẫn dùng được.",
        )
    audit.succeed(interpretation.model_dump(mode="json"))
    return interpretation, "success", None


def analyze_quality_intelligence(
    dataframe: pd.DataFrame,
    *,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    production_unit: str = "unit",
    measurement_unit: str = "unit",
    column_mapping: dict[str, str] | None = None,
    include_ai: bool = True,
) -> dict[str, Any]:
    normalized_measurement_unit = measurement_unit.strip()
    if not normalized_measurement_unit or len(normalized_measurement_unit) > 40:
        raise ValueError("Measurement unit không hợp lệ.")
    dataset: ManufacturingDataset = normalize_manufacturing_dataframe(
        dataframe,
        column_mapping=column_mapping,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        production_unit=production_unit,
        require_production=False,
    )
    records = dataset.records
    evidence: list[dict[str, Any]] = []
    inspected_records = [
        record for record in records if record.inspected_quantity is not None
    ]
    total_inspected = (
        sum(float(record.inspected_quantity or 0) for record in inspected_records)
        if inspected_records
        else None
    )
    defect_values = [
        _defect_events(record)
        for record in records
        if _defect_events(record) is not None
    ]
    total_defects = (
        sum(float(value or 0) for value in defect_values)
        if defect_values
        else None
    )
    defect_rate, defect_rate_count, defect_rate_basis = _defect_rate(records)
    first_pass_yield, fpy_count, fpy_basis = _first_pass_rate(records)
    rework_rate, rework_count, rework_basis = _complete_rate(
        records,
        numerator_field="reworked_units",
    )
    scrap_rate, scrap_count, scrap_basis = _complete_rate(
        records,
        numerator_field="scrapped_units",
    )
    rty, rty_basis = _rolled_throughput_yield(records)

    kpi_values: dict[str, tuple[float | None, str, int, str]] = {
        "total_inspected": (
            total_inspected,
            "units",
            len(inspected_records),
            "sum_of_explicit_inspected_quantity",
        ),
        "total_defects": (
            total_defects,
            "defects",
            len(defect_values),
            "sum_of_explicit_defects_or_one_per_classified_row",
        ),
        "defect_rate": (
            defect_rate,
            "percent",
            defect_rate_count,
            defect_rate_basis,
        ),
        "fpy": (
            first_pass_yield,
            "percent",
            fpy_count,
            fpy_basis,
        ),
        "rty": (
            rty,
            "percent",
            len(records) if rty is not None else 0,
            rty_basis,
        ),
        "rework_rate": (
            rework_rate,
            "percent",
            rework_count,
            rework_basis,
        ),
        "scrap_rate": (
            scrap_rate,
            "percent",
            scrap_count,
            scrap_basis,
        ),
    }
    kpi_results: dict[str, dict[str, Any]] = {}
    for name, (value, unit, count, basis) in kpi_values.items():
        evidence_id = (
            _add_evidence(
                evidence,
                metric=name,
                value=_rounded(value),
                unit=unit,
                method=basis,
                record_count=count,
            )
            if value is not None
            else None
        )
        kpi_results[name] = _metric(value, unit, basis, count, evidence_id)

    evidence_scope_id = _add_evidence(
        evidence,
        metric="quality_data_scope",
        value={
            "rows_used": len(records),
            "inspected_records": len(inspected_records),
            "defect_records": len(defect_values),
        },
        unit="records",
        method="normalized_valid_manufacturing_records",
        record_count=len(records),
    )
    pareto = _pareto(records, evidence)
    by_product = _breakdown(records, "product", evidence)
    by_process = _breakdown(records, "process", evidence)
    by_line = _breakdown(records, "line", evidence)
    by_shift = _breakdown(records, "shift", evidence)
    daily = _periods(
        records,
        "day",
        source_timezone=dataset.source_timezone,
    )
    weekly = _periods(
        records,
        "week",
        source_timezone=dataset.source_timezone,
    )
    monthly = _periods(
        records,
        "month",
        source_timezone=dataset.source_timezone,
    )
    trend = _quality_trend(daily, evidence)
    spc = _spc(
        records,
        evidence,
        measurement_unit=normalized_measurement_unit,
    )
    anomalies = _quality_anomalies(daily, evidence) + spc["anomalies"]

    statuses: dict[str, int] = defaultdict(int)
    for record in records:
        statuses[_normalize_status(record.quality_status)] += 1
    status_counts = [
        {"status": status, "record_count": count}
        for status, count in sorted(statuses.items())
    ]

    limitations = list(dataset.data_quality.warnings)
    if total_inspected is None:
        limitations.append(
            "Thiếu Inspected Quantity; các KPI cần mẫu số không được suy diễn."
        )
    if defect_rate is None:
        limitations.append(
            "Defect Rate cần Inspected Quantity và độ phủ đầy đủ của Defective Units hoặc Defects."
        )
    elif defect_rate_basis == "defect_events_per_inspected_item":
        limitations.append(
            "Defect Rate dùng số lần lỗi trên số lượng kiểm tra; có thể vượt 100% nếu một sản phẩm có nhiều lỗi."
        )
    if first_pass_yield is None:
        limitations.append(
            "FPY cần First Pass Inspected và First Pass Passed; không suy ra từ trạng thái cuối cùng."
        )
    if rty is None:
        limitations.append(
            "RTY cần số lượng pass lần đầu và thứ tự công đoạn nhất quán cho cùng luồng sản phẩm."
        )
    if rework_rate is None:
        limitations.append("Rework Rate cần Reworked Units và Inspected Quantity đầy đủ.")
    if scrap_rate is None:
        limitations.append("Scrap Rate cần Scrapped Units và Inspected Quantity đầy đủ.")
    if not pareto:
        limitations.append("Không có số lỗi hoặc phân loại lỗi để lập Pareto.")
    if trend["direction"] == "insufficient_data":
        limitations.append(
            "Cần ít nhất bốn kỳ ngày có defect rate để đánh giá xu hướng chất lượng."
        )
    if spc["process_stability"]["status"] == "insufficient_data":
        limitations.append(
            "Chưa đủ dữ liệu nhóm mẫu để kết luận ổn định SPC; mọi biểu đồ hiện có dưới ngưỡng 20 nhóm chỉ mang tính sơ bộ."
        )
    if not any(record.measurement_value is not None for record in records):
        limitations.append(
            "Thiếu Measurement Value, Subgroup ID hoặc Batch; X-bar/R và capability indices không được suy diễn."
        )
    if not any(
        record.lower_spec_limit is not None
        and record.upper_spec_limit is not None
        for record in records
    ):
        limitations.append(
            "Thiếu Lower Spec Limit hoặc Upper Spec Limit; Cp/Cpk không được tính."
        )

    management_answers: dict[str, dict[str, Any]] = {}
    top_defect = pareto[0] if pareto else None
    highest_process = next(
        (row for row in by_process if row["defect_rate_percent"] is not None),
        None,
    )
    highest_product = next(
        (row for row in by_product if row["defect_rate_percent"] is not None),
        None,
    )
    management_answers["top_defect"] = (
        {
            "answer": (
                f"{top_defect['category']} là nhóm lỗi xuất hiện nhiều nhất."
            ),
            "evidence_ids": [top_defect["evidence_id"]],
        }
        if top_defect
        else {
            "answer": "Chưa có dữ liệu phân loại lỗi.",
            "evidence_ids": [evidence_scope_id],
        }
    )
    management_answers["highest_defect_process"] = (
        {
            "answer": (
                f"{highest_process['process']} có defect rate cao nhất trong "
                "các công đoạn có mẫu số hợp lệ."
            ),
            "evidence_ids": [highest_process["evidence_id"]],
        }
        if highest_process
        else {
            "answer": "Chưa đủ dữ liệu để so sánh defect rate theo công đoạn.",
            "evidence_ids": [evidence_scope_id],
        }
    )
    management_answers["quality_trend"] = {
        "answer": {
            "declining_quality": "Tỷ lệ lỗi có xu hướng tăng.",
            "improving_quality": "Tỷ lệ lỗi có xu hướng giảm.",
            "stable": "Tỷ lệ lỗi tương đối ổn định theo quy tắc so sánh hiện tại.",
            "insufficient_data": "Chưa đủ dữ liệu theo thời gian để đánh giá xu hướng.",
        }[trend["direction"]],
        "evidence_ids": (
            [trend["evidence_id"]]
            if trend["evidence_id"]
            else [evidence_scope_id]
        ),
    }
    management_answers["risky_products"] = (
        {
            "answer": (
                f"{highest_product['product']} cần được ưu tiên kiểm tra theo "
                "defect rate quan sát được."
            ),
            "evidence_ids": [highest_product["evidence_id"]],
        }
        if highest_product
        else {
            "answer": "Chưa đủ dữ liệu để xếp hạng rủi ro sản phẩm.",
            "evidence_ids": [evidence_scope_id],
        }
    )
    management_answers["statistical_control"] = {
        "answer": (
            "Có điểm ngoài giới hạn kiểm soát 3-sigma."
            if spc["process_stability"]["status"] == "out_of_control"
            else "Các biểu đồ đủ baseline hiện không có điểm ngoài giới hạn."
            if spc["process_stability"]["status"] == "stable"
            else "Chưa đủ baseline để kết luận trạng thái kiểm soát thống kê."
        ),
        "evidence_ids": [
            chart["evidence_id"]
            for item in spc["charts"]["x_bar_r"]
            for chart in (item["x_bar"], item["r"])
            if chart.get("evidence_id")
        ]
        + [
            chart["evidence_id"]
            for chart_type in ("p", "c", "u")
            for chart in spc["charts"][chart_type]
            if chart.get("evidence_id")
        ]
        or [evidence_scope_id],
    }
    recommendations: list[dict[str, Any]] = []
    if top_defect:
        recommendations.append(
            {
                "action": (
                    f"Kiểm tra mẫu và nguyên nhân của nhóm lỗi "
                    f"{top_defect['category']} trước khi thay đổi quy trình."
                ),
                "priority": "high" if top_defect["dominant"] else "medium",
                "evidence_ids": [top_defect["evidence_id"]],
            }
        )
    if spc["process_stability"]["status"] == "out_of_control":
        recommendations.append(
            {
                "action": "Khoanh vùng các điểm SPC ngoài giới hạn và xác minh nguyên nhân đặc biệt.",
                "priority": "high",
                "evidence_ids": [
                    item
                    for anomaly in spc["anomalies"]
                    for item in anomaly["evidence_ids"]
                ],
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "action": "Bổ sung dữ liệu kiểm tra, mẫu số và phân nhóm hợp lý trước khi đưa ra quyết định chất lượng.",
                "priority": "medium",
                "evidence_ids": [evidence_scope_id],
            }
        )

    quality: dict[str, Any] = {
        "schema_version": "2.2",
        "executive_summary": (
            f"Đã phân tích {len(records)} dòng hợp lệ; "
            f"{'tổng lỗi ghi nhận là ' + format(total_defects, 'g') if total_defects is not None else 'chưa có tổng lỗi đủ cơ sở'}."
        ),
        "data_quality": dataset.data_quality.model_dump(mode="json"),
        "data_contract": dataset.model_dump(
            mode="json",
            exclude={"records"},
        ),
        "data_structure": {
            "product_count": len({record.product for record in records if record.product}),
            "process_count": len({record.process for record in records if record.process}),
            "line_count": len({record.line for record in records if record.line}),
            "shift_count": len({record.shift for record in records if record.shift}),
            "defect_category_count": len(
                {category for record in records if (category := _defect_category(record))}
            ),
            "measurement_count": sum(
                record.measurement_value is not None for record in records
            ),
            "subgroup_count": len(
                {
                    record.subgroup_id or record.batch
                    for record in records
                    if record.subgroup_id or record.batch
                }
            ),
        },
        "quality_status_distribution": status_counts,
        "kpi_results": kpi_results,
        "defect_pareto": pareto,
        "quality_by_product": by_product,
        "quality_by_process": by_process,
        "quality_by_line": by_line,
        "quality_by_shift": by_shift,
        "quality_trends": {
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
        },
        "quality_trend": trend,
        "spc": spc,
        "quality_anomalies": anomalies,
        "management_answers": management_answers,
        "recommendations": recommendations,
        "evidence_references": evidence,
        "analysis_limitations": list(dict.fromkeys(limitations)),
        "units": {
            "production": dataset.units["production"],
            "measurement": normalized_measurement_unit,
            "defect_rate": "percent",
        },
    }
    if include_ai:
        interpretation, ai_status, ai_error = _interpret_with_gemini(quality)
        quality["ai_status"] = ai_status
        quality["ai_error"] = ai_error
        quality["ai_interpretation"] = (
            interpretation.model_dump(mode="json")
            if interpretation
            else None
        )
    else:
        quality["ai_status"] = "not_requested"
        quality["ai_error"] = None
        quality["ai_interpretation"] = None
    return quality
