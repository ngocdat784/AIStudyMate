from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections import defaultdict
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
_MIN_CORRELATION_SAMPLES = 5
_MIN_FACTOR_INSPECTIONS = 5
_MAX_EVIDENCE = 1000
_MAX_GROUPS = 10000
_MAX_CORRELATION_VARIABLES = 30
_ENVIRONMENT_ATTRIBUTE_PATTERN = re.compile(
    r"ambient|temperature|humidity|moisture|pressure|vibration|noise|weather|environment",
    re.IGNORECASE,
)

_METRIC_LABELS = {
    "actual_production": "Actual Production",
    "planned_production": "Planned Production",
    "production_achievement_rate": "Production Achievement Rate",
    "inspected_quantity": "Inspected Quantity",
    "defective_units": "Defective Units",
    "defects": "Defect Events",
    "defect_rate": "Defect Rate",
    "downtime_minutes": "Downtime",
    "cycle_time_seconds": "Cycle Time",
    "waiting_time_minutes": "Waiting Time",
    "operating_time_minutes": "Operating Time",
    "available_time_minutes": "Available Time",
    "reworked_units": "Reworked Units",
    "scrapped_units": "Scrapped Units",
    "measurement_value": "Measurement Value",
}
_DIMENSIONS = ("product", "process", "line", "shift", "machine", "material")
_FACTOR_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "Man": (("Operator", "operator"), ("Shift", "shift")),
    "Machine": (
        ("Machine", "machine"),
        ("Line", "line"),
        ("Downtime", "downtime_minutes"),
    ),
    "Method": (
        ("Method", "method"),
        ("Process", "process"),
        ("Cycle Time", "cycle_time_seconds"),
        ("Waiting Time", "waiting_time_minutes"),
        ("Downtime", "downtime_minutes"),
    ),
    "Material": (
        ("Material", "material"),
        ("Supplier", "supplier"),
        ("Product", "product"),
        ("Component", "component"),
        ("Batch", "batch"),
    ),
    "Measurement": (
        ("Measurement Characteristic", "measurement_name"),
        ("Measurement Value", "measurement_value"),
        ("Quality Status", "quality_status"),
        ("Defect Type", "defect_type"),
        ("Inspection Counts", "inspected_quantity"),
        ("Defect Counts", "defective_units"),
    ),
    "Mother Nature / Environment": (("Environment", "environment"),),
}
_VARIABLE_ALIASES = {
    "actual_production": "actual_production",
    "production": "actual_production",
    "planned_production": "planned_production",
    "production_achievement_rate": "production_achievement_rate",
    "inspected_quantity": "inspected_quantity",
    "defective_units": "defective_units",
    "defects": "defects",
    "defect_rate": "defect_rate",
    "downtime_minutes": "downtime_minutes",
    "cycle_time_seconds": "cycle_time_seconds",
    "waiting_time_minutes": "waiting_time_minutes",
    "operating_time_minutes": "operating_time_minutes",
    "available_time_minutes": "available_time_minutes",
    "reworked_units": "reworked_units",
    "scrapped_units": "scrapped_units",
    "measurement_value": "measurement_value",
}


class EvidenceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class RootCauseAIReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_statement: EvidenceStatement
    evidence_summary: list[EvidenceStatement] = Field(max_length=10)
    fishbone_analysis: list[EvidenceStatement] = Field(max_length=12)
    five_whys: list[EvidenceStatement] = Field(min_length=5, max_length=5)
    root_cause_candidates: list[EvidenceStatement] = Field(max_length=10)
    corrective_actions: list[EvidenceStatement] = Field(max_length=10)
    confidence_assessment: EvidenceStatement
    verification_plan: EvidenceStatement
    limitations: list[str] = Field(max_length=10)


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for index in order[start:end]:
            ranks[index] = average_rank
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    if left_ss <= 0 or right_ss <= 0:
        return None
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    return max(-1.0, min(1.0, covariance / math.sqrt(left_ss * right_ss)))


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_rank(left), _rank(right))


def _add_evidence(
    evidence: list[dict[str, Any]],
    *,
    metric: str,
    value: Any,
    method: str,
    record_count: int,
    dimensions: dict[str, str] | None = None,
    unit: str = "value",
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
    evidence.append(item)
    return evidence_id


def _defect_counts(
    records: list[ManufacturingRecord],
) -> tuple[float | None, float | None, str]:
    inspected = [record for record in records if record.inspected_quantity is not None]
    if not inspected:
        return None, None, "missing_inspected_quantity"
    inspected_total = sum(record.inspected_quantity or 0 for record in inspected)
    has_units = any(record.defective_units is not None for record in inspected)
    if has_units:
        if any(record.defective_units is None for record in inspected):
            return inspected_total, None, "incomplete_defective_units_coverage"
        return (
            inspected_total,
            sum(record.defective_units or 0 for record in inspected),
            "defective_units",
        )
    if all(record.defects is not None for record in inspected):
        return (
            inspected_total,
            sum(record.defects or 0 for record in inspected),
            "defect_events",
        )
    return inspected_total, None, "incomplete_defect_event_coverage"


def _time_label(
    record: ManufacturingRecord,
    period: str,
    source_timezone: ZoneInfo,
) -> str | None:
    timestamp = record.timestamp
    if timestamp is None:
        return None
    timestamp = timestamp.astimezone(source_timezone)
    if period == "month":
        return timestamp.strftime("%Y-%m")
    if period == "week":
        iso = timestamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return timestamp.date().isoformat()


def _aggregate_group(
    records: list[ManufacturingRecord],
    mapped_targets: set[str],
) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {name: None for name in _METRIC_LABELS}
    if "Production" in mapped_targets:
        metrics["actual_production"] = sum(record.production for record in records)

    for target, metric, field in (
        ("Planned Production", "planned_production", "planned_production"),
        ("Inspected Quantity", "inspected_quantity", "inspected_quantity"),
        ("Defective Units", "defective_units", "defective_units"),
        ("Defects", "defects", "defects"),
        ("Downtime", "downtime_minutes", "downtime_minutes"),
        ("Cycle Time", "cycle_time_seconds", "cycle_time_seconds"),
        ("Waiting Time", "waiting_time_minutes", "waiting_time_minutes"),
        ("Operating Time", "operating_time_minutes", "operating_time_minutes"),
        ("Available Time", "available_time_minutes", "available_time_minutes"),
        ("Reworked Units", "reworked_units", "reworked_units"),
        ("Scrapped Units", "scrapped_units", "scrapped_units"),
        ("Measurement Value", "measurement_value", "measurement_value"),
    ):
        if target not in mapped_targets:
            continue
        values = [
            float(value)
            for record in records
            if (value := getattr(record, field)) is not None
        ]
        if not values:
            continue
        metrics[metric] = (
            statistics.fmean(values)
            if metric in {"cycle_time_seconds", "measurement_value"}
            else sum(values)
        )

    inspected, defect_count, defect_basis = _defect_counts(records)
    metrics["inspected_quantity"] = inspected
    metrics["defective_units"] = (
        defect_count if defect_basis == "defective_units" else None
    )
    metrics["defects"] = defect_count if defect_basis == "defect_events" else None
    metrics["defect_rate"] = (
        defect_count / inspected * 100
        if inspected is not None and defect_count is not None and inspected > 0
        else None
    )
    if (
        metrics["actual_production"] is not None
        and metrics["planned_production"] is not None
        and metrics["planned_production"] > 0
    ):
        metrics["production_achievement_rate"] = (
            metrics["actual_production"] / metrics["planned_production"] * 100
        )

    numeric_attributes: dict[str, list[float]] = defaultdict(list)
    for record in records:
        for name, value in record.attributes.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                numeric_attributes[f"attribute:{name}"].append(float(value))
    for name, values in numeric_attributes.items():
        metrics[name] = statistics.fmean(values)
    return metrics


def _group_records(
    records: list[ManufacturingRecord],
    *,
    time_bucket: str,
    dimensions: list[str],
    mapped_targets: set[str],
    source_timezone: str,
    production_record_ids: set[int],
    quality_record_ids: set[int],
    production_targets: set[str],
    quality_targets: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[ManufacturingRecord]] = defaultdict(list)
    has_timestamps = "Date" in mapped_targets
    timezone_info = ZoneInfo(source_timezone)
    for record in records:
        key: list[str] = []
        if has_timestamps:
            key.append(_time_label(record, time_bucket, timezone_info) or "Undated")
        for dimension in dimensions:
            value = getattr(record, dimension)
            key.append(str(value).strip() if value else "Unspecified")
        grouped[tuple(key)].append(record)
    if len(grouped) > _MAX_GROUPS:
        raise ValueError(
            f"Linked analysis produced more than {_MAX_GROUPS} groups; "
            "reduce the selected grouping dimensions or time range."
        )

    observations: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        has_production = any(id(record) in production_record_ids for record in group)
        has_quality = any(id(record) in quality_record_ids for record in group)
        scope: dict[str, str] = {}
        key_index = 0
        if has_timestamps:
            scope[time_bucket] = key[0]
            key_index = 1
        scope.update(
            {
                dimension: key[key_index + index]
                for index, dimension in enumerate(dimensions)
            }
        )
        group_targets = set(mapped_targets)
        if not has_production:
            group_targets.difference_update(production_targets)
        if not has_quality:
            group_targets.difference_update(quality_targets)
        metrics = _aggregate_group(group, group_targets)
        observations.append(
            {
                "scope": scope,
                "metrics": metrics,
                "record_count": len(group),
                "has_production": has_production,
                "has_quality": has_quality,
                "records": group,
            }
        )
    return observations


def _correlations(
    observations: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    evidence_by_group: list[str | None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    all_variables = sorted(
        {
            metric
            for observation in observations
            for metric, value in observation["metrics"].items()
            if value is not None
        }
    )
    variables = sorted(
        all_variables,
        key=lambda name: (name.startswith("attribute:"), name),
    )[:_MAX_CORRELATION_VARIABLES]
    pearson_matrix: list[list[float | None]] = []
    spearman_matrix: list[list[float | None]] = []
    sample_matrix: list[list[int]] = []
    notable: list[dict[str, Any]] = []
    for left_index, left_name in enumerate(variables):
        pearson_row: list[float | None] = []
        spearman_row: list[float | None] = []
        sample_row: list[int] = []
        for right_index, right_name in enumerate(variables):
            pairs = [
                (float(item["metrics"][left_name]), float(item["metrics"][right_name]), index)
                for index, item in enumerate(observations)
                if item["metrics"].get(left_name) is not None
                and item["metrics"].get(right_name) is not None
            ]
            left = [pair[0] for pair in pairs]
            right = [pair[1] for pair in pairs]
            pearson_value = _pearson(left, right)
            spearman_value = _spearman(left, right)
            pearson_row.append(_rounded(pearson_value))
            spearman_row.append(_rounded(spearman_value))
            sample_row.append(len(pairs))

            if (
                left_index < right_index
                and len(pairs) >= _MIN_CORRELATION_SAMPLES
                and pearson_value is not None
                and spearman_value is not None
                and max(abs(pearson_value), abs(spearman_value)) >= 0.3
            ):
                correlation_evidence = _add_evidence(
                    evidence,
                    metric=f"correlation:{left_name}:{right_name}",
                    value={
                        "pearson": _rounded(pearson_value),
                        "spearman": _rounded(spearman_value),
                    },
                    method="pairwise_complete_observations",
                    record_count=len(pairs),
                    unit="correlation_coefficient",
                )
                sample_evidence = [
                    evidence_by_group[pair[2]]
                    for pair in pairs[:5]
                    if evidence_by_group[pair[2]] is not None
                ]
                notable.append(
                    {
                        "left_variable": left_name,
                        "right_variable": right_name,
                        "pearson": _rounded(pearson_value),
                        "spearman": _rounded(spearman_value),
                        "sample_count": len(pairs),
                        "evidence_ids": [
                            correlation_evidence,
                            *sample_evidence[:5],
                        ],
                    }
                )
        pearson_matrix.append(pearson_row)
        spearman_matrix.append(spearman_row)
        sample_matrix.append(sample_row)
    return (
        {
            "variables": variables,
            "pearson": pearson_matrix,
            "spearman": spearman_matrix,
            "pairwise_sample_counts": sample_matrix,
            "method": "pairwise_complete; Spearman uses average ranks for ties",
            "omitted_variable_count": max(0, len(all_variables) - len(variables)),
        },
        notable,
    )


def _factor_statistics(
    records: list[ManufacturingRecord],
    *,
    field: str,
    mapped_targets: set[str],
    production_record_ids: set[int],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        value = getattr(record, field, None)
        if value is not None and str(value).strip():
            groups[str(value).strip()].append(record)
    result: list[dict[str, Any]] = []
    for label, group in groups.items():
        inspected, defects, defect_basis = _defect_counts(group)
        production_records = [
            record for record in group if id(record) in production_record_ids
        ]
        production = (
            sum(record.production for record in production_records)
            if "Production" in mapped_targets and production_records
            else None
        )
        rate = (
            defects / inspected * 100
            if inspected is not None and defects is not None and inspected > 0
            else None
        )
        result.append(
            {
                "field": field,
                "level": label,
                "inspected": _rounded(inspected),
                "defects": _rounded(defects),
                "defect_count_basis": defect_basis,
                "defect_rate_percent": _rounded(rate),
                "production": _rounded(production),
                "record_count": len(group),
            }
        )
    selected = sorted(
        result,
        key=lambda item: (
            item["defect_rate_percent"] is None,
            -(item["defect_rate_percent"] or 0),
            item["level"].casefold(),
        ),
    )[:10]
    for item in selected:
        evidence_id = _add_evidence(
            evidence,
            metric=f"factor:{field}",
            value={
                "level": item["level"],
                "inspected": item["inspected"],
                "defects": item["defects"],
                "defect_count_basis": item["defect_count_basis"],
                "defect_rate_percent": item["defect_rate_percent"],
                "production": item["production"],
            },
            method="stratified_summary_of_normalized_records",
            record_count=item["record_count"],
            dimensions={field: item["level"]},
        )
        item["evidence_ids"] = [evidence_id]
    return selected


def _fishbone(
    records: list[ManufacturingRecord],
    mapped_targets: set[str],
    production_record_ids: set[int],
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    categories: list[dict[str, Any]] = []
    factor_findings: list[dict[str, Any]] = []
    numeric_fields = {
        "cycle_time_seconds",
        "waiting_time_minutes",
        "downtime_minutes",
        "inspected_quantity",
        "defective_units",
        "measurement_value",
    }
    for category, fields in _FACTOR_GROUPS.items():
        available: list[dict[str, Any]] = []
        missing_fields: list[str] = []
        for label, field in fields:
            mapped = (
                label in mapped_targets
                or field in {
                    "cycle_time_seconds",
                    "waiting_time_minutes",
                    "downtime_minutes",
                    "inspected_quantity",
                    "defective_units",
                    "measurement_value",
                }
                and any(
                    getattr(record, field, None) is not None
                    for record in records
                )
            )
            if not mapped:
                missing_fields.append(label)
                continue
            if field in numeric_fields:
                values = [
                    float(value)
                    for record in records
                    if (value := getattr(record, field, None)) is not None
                ]
                if values:
                    summary = {
                        "count": len(values),
                        "mean": _rounded(statistics.fmean(values)),
                        "minimum": _rounded(min(values)),
                        "maximum": _rounded(max(values)),
                    }
                    evidence_id = _add_evidence(
                        evidence,
                        metric=f"fishbone_measurement:{field}",
                        value=summary,
                        method="descriptive_statistics_of_normalized_measurements",
                        record_count=len(values),
                        unit="normalized_source_unit",
                    )
                    available.append(
                        {
                            "field": label,
                            "numeric_summary": summary,
                            "evidence_ids": [evidence_id],
                        }
                    )
                continue
            findings = _factor_statistics(
                records,
                field=field,
                mapped_targets=mapped_targets,
                production_record_ids=production_record_ids,
                evidence=evidence,
            )
            if findings:
                available.append(
                    {
                        "field": label,
                        "levels": findings,
                    }
                )
                factor_findings.extend(
                    {
                        **item,
                        "fishbone_category": category,
                    }
                    for item in findings
                )
        if category == "Mother Nature / Environment":
            attribute_names = sorted(
                {
                    name
                    for record in records
                    for name in record.attributes
                    if _ENVIRONMENT_ATTRIBUTE_PATTERN.search(name)
                }
            )
            for name in attribute_names:
                values = [
                    float(value)
                    for record in records
                    if (value := record.attributes.get(name)) is not None
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                ]
                if not values:
                    continue
                summary = {
                    "count": len(values),
                    "mean": _rounded(statistics.fmean(values)),
                    "minimum": _rounded(min(values)),
                    "maximum": _rounded(max(values)),
                }
                evidence_id = _add_evidence(
                    evidence,
                    metric=f"environment_attribute:{name}",
                    value=summary,
                    method="descriptive_statistics_of_numeric_source_attribute",
                    record_count=len(values),
                    dimensions={"attribute": name},
                    unit="source_unit_unspecified",
                )
                available.append(
                    {
                        "field": name,
                        "numeric_summary": summary,
                        "evidence_ids": [evidence_id],
                    }
                )
        categories.append(
            {
                "category": category,
                "factor_fields": available,
                "missing_inputs": missing_fields,
                "status": "evidence_available" if available else "missing_data",
            }
        )
    return categories, factor_findings


def _candidate_text(
    variable: str,
    outcome: str,
    coefficient: float,
) -> str:
    factor_name = _METRIC_LABELS.get(variable, variable)
    outcome_name = _METRIC_LABELS.get(outcome, outcome)
    direction = "tương quan thuận" if coefficient > 0 else "tương quan nghịch"
    return (
        f"{factor_name} có {direction} với {outcome_name} "
        "trong các nhóm dữ liệu đã liên kết."
    )


def _rank_hypotheses(
    correlations: list[dict[str, Any]],
    factor_findings: list[dict[str, Any]],
    *,
    outcome_metric: str,
    overall_defect_rate: float | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for result in correlations:
        if outcome_metric not in (
            result["left_variable"],
            result["right_variable"],
        ):
            continue
        predictor = (
            result["right_variable"]
            if result["left_variable"] == outcome_metric
            else result["left_variable"]
        )
        coefficient = (
            result["spearman"]
            if abs(result["spearman"]) >= abs(result["pearson"])
            else result["pearson"]
        )
        if coefficient is None:
            continue
        score = round(abs(coefficient) * 100)
        candidates.append(
            {
                "hypothesis": _candidate_text(
                    predictor,
                    outcome_metric,
                    coefficient,
                ),
                "factor": predictor,
                "factor_level": None,
                "outcome_metric": outcome_metric,
                "direction": "positive" if coefficient > 0 else "negative",
                "support_score": score,
                "support_basis": "absolute correlation coefficient × 100",
                "sample_count": result["sample_count"],
                "evidence_ids": result["evidence_ids"],
                "confirming_test": (
                    "So sánh các nhóm khớp theo thời gian/sản phẩm/công đoạn/ca "
                    "và xác minh bằng thử nghiệm có kiểm soát hoặc phân tầng."
                ),
                "missing_data": [],
                "causality_status": "association_only",
            }
        )

    if outcome_metric == "defect_rate":
        for factor in factor_findings:
            rate = factor["defect_rate_percent"]
            inspected = factor["inspected"]
            if (
                rate is None
                or inspected is None
                or inspected < _MIN_FACTOR_INSPECTIONS
                or overall_defect_rate is None
                or rate <= overall_defect_rate
            ):
                continue
            lift = (rate - overall_defect_rate) / max(
                abs(overall_defect_rate),
                0.01,
            )
            score = min(100, round(lift * 50))
            candidates.append(
                {
                    "hypothesis": (
                        f"Mức {factor['level']} của yếu tố {factor['field']} "
                        "có defect rate quan sát cao hơn baseline chung."
                    ),
                    "factor": factor["field"],
                    "factor_level": factor["level"],
                    "outcome_metric": outcome_metric,
                    "direction": "higher",
                    "support_score": score,
                    "support_basis": (
                        "heuristic relative lift over the overall defect rate; "
                        "not a probability"
                    ),
                    "sample_count": factor["record_count"],
                    "evidence_ids": factor["evidence_ids"],
                    "confirming_test": (
                        "Kiểm tra lặp lại và so sánh cùng cơ cấu sản phẩm/công "
                        "đoạn với các mức yếu tố khác."
                    ),
                    "missing_data": [],
                    "causality_status": "association_only",
                }
            )

    candidates.sort(
        key=lambda item: (
            -item["support_score"],
            -item["sample_count"],
            item["factor"].casefold(),
        )
    )
    return [
        {"id": f"H{index:03d}", **candidate}
        for index, candidate in enumerate(candidates[:10], start=1)
    ]


def _action_for_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    factor = candidate["factor"]
    role_action = {
        "operator": ("Giám sát sản xuất", "Rà soát đào tạo và việc tuân thủ hướng dẫn công việc."),
        "shift": ("Giám sát sản xuất", "Đối chiếu bàn giao ca và điều kiện bố trí nhân lực."),
        "machine": ("Kỹ sư bảo trì", "Kiểm tra tình trạng máy và lịch sử bảo trì."),
        "line": ("Kỹ sư quy trình", "Đối chiếu thiết lập, tải và điều kiện vận hành dây chuyền."),
        "process": ("Kỹ sư quy trình", "Rà soát công đoạn, phương pháp và kế hoạch kiểm soát."),
        "method": ("Kỹ sư quy trình", "Xác minh hướng dẫn công việc và mức độ tuân thủ."),
        "material": ("Kỹ sư chất lượng / nhà cung cấp", "Truy xuất lô vật liệu và xác minh chất lượng đầu vào."),
        "supplier": ("Kỹ sư chất lượng nhà cung cấp", "So sánh lô nhà cung cấp và bằng chứng kiểm tra đầu vào."),
        "product": ("Kỹ sư sản phẩm", "Phân tầng kết quả theo thiết kế và phiên bản sản phẩm."),
        "batch": ("Kỹ sư chất lượng", "Khoanh vùng và kiểm tra lô liên quan."),
        "downtime_minutes": ("Kỹ sư bảo trì", "Đối chiếu sự kiện dừng máy với các khoảng thời gian bị ảnh hưởng."),
        "cycle_time_seconds": ("Kỹ sư quy trình", "Quan sát điểm giới hạn cycle time tại công đoạn liên quan."),
        "waiting_time_minutes": ("Điều độ sản xuất", "Truy tìm nguyên nhân hàng đợi và thời gian chờ giữa công đoạn."),
        "measurement_value": ("Kỹ sư đo lường", "Xác minh hệ thống đo và tình trạng hiệu chuẩn."),
        "defective_units": ("Kỹ sư chất lượng", "Xác minh phạm vi kiểm tra và phân loại lỗi."),
    }
    if isinstance(factor, str) and factor.startswith("attribute:"):
        owner_role, action = (
            ("Cơ sở vật chất / EHS", "Xác minh tín hiệu môi trường và nguồn đo đã hiệu chuẩn.")
            if _ENVIRONMENT_ATTRIBUTE_PATTERN.search(factor)
            else (
                "Kỹ sư quy trình",
                "Xác thực trường dữ liệu và kiểm tra phân tầng trên các nhóm tương đồng.",
            )
        )
    else:
        owner_role, action = role_action.get(
            factor,
            ("Kỹ sư quy trình", "Kiểm tra phân tầng yếu tố này trước khi thay đổi quy trình."),
        )
    priority = (
        "high"
        if candidate["support_score"] >= 70
        else "medium"
        if candidate["support_score"] >= 40
        else "low"
    )
    return {
        "candidate_id": candidate["id"],
        "action": action,
        "priority": priority,
        "suggested_owner_role": owner_role,
        "assigned_owner_user_id": None,
        "suggested_due_days": 7 if priority == "high" else 14,
        "expected_impact": (
            "Có thể giảm tổn thất mục tiêu nếu giả thuyết được xác nhận; "
            "không ước lượng tác động số chỉ từ tương quan."
        ),
        "verification_plan": candidate["confirming_test"],
        "evidence_ids": candidate["evidence_ids"],
    }


def _validate_ai_report(
    report: RootCauseAIReport,
    evidence_ids: set[str],
) -> None:
    statements = [
        report.problem_statement,
        *report.evidence_summary,
        *report.fishbone_analysis,
        *report.five_whys,
        *report.root_cause_candidates,
        *report.corrective_actions,
        report.confidence_assessment,
        report.verification_plan,
    ]
    for statement in statements:
        if not set(statement.evidence_ids).issubset(evidence_ids):
            raise ValueError("AI report references an unknown evidence ID.")
        if any(character.isdigit() for character in statement.text):
            raise ValueError("AI report must link numeric claims by evidence ID.")
    if any(any(character.isdigit() for character in item) for item in report.limitations):
        raise ValueError("AI limitation contains an unsupported number.")


def _interpret_with_gemini(
    report_context: dict[str, Any],
) -> tuple[RootCauseAIReport | None, str, str | None]:
    if not settings.GEMINI_API_KEY:
        return (
            None,
            "not_configured",
            "Gemini is not configured; deterministic analysis remains available.",
        )
    evidence_ids = {
        item["id"] for item in report_context["evidence_references"]
    }
    if not evidence_ids:
        return None, "insufficient_evidence", "No evidence is available for AI interpretation."

    from app.services.hardware_ai_service import (
        GEMINI_MODEL,
        _gemini_response_schema,
        get_client,
    )

    prompt = (
        "Viết báo cáo điều tra nguyên nhân ngắn gọn bằng tiếng Việt. Chỉ dùng "
        "kết quả chuẩn hóa và evidence được cung cấp; không tạo nguyên nhân, "
        "phép đo, ngày, ngưỡng hoặc số liệu mới. Mọi nhận định phải dẫn "
        "evidence ID tồn tại và không viết chữ số. Tương quan không chứng minh "
        "nhân quả. Năm câu trả lời 5 Whys là giả thuyết cần kiểm chứng, không "
        "phải nguyên nhân đã xác nhận. Trả đúng năm bước 5 Whys, nêu dữ liệu "
        "còn thiếu và kế hoạch xác minh. Trả JSON theo schema.\n"
        + json.dumps(report_context, ensure_ascii=False, allow_nan=False)
    )
    audit = AIAuditContext(
        analysis_type="root_cause_intelligence",
        model_name=GEMINI_MODEL,
        prompt_version="root_cause_intelligence_v1",
        input_data=report_context,
        prompt_text=prompt,
    )
    try:
        response = call_gemini_with_retry(
            lambda: get_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": _gemini_response_schema(RootCauseAIReport),
                    "temperature": 0.2,
                },
            )
        )
    except Exception as error:
        audit.fail(error)
        logger.exception("Root cause Gemini request failed")
        return None, "error", "Gemini is unavailable; Python analysis was retained."

    audit.set_usage_from_response(response)
    raw_response = getattr(response, "text", None)
    audit.set_raw_response(raw_response)
    try:
        if not raw_response or not raw_response.strip():
            raise ValueError("Gemini returned an empty root-cause report.")
        report = RootCauseAIReport.model_validate_json(raw_response)
        _validate_ai_report(report, evidence_ids)
    except Exception as error:
        audit.fail(error, validation_failed=True)
        logger.warning(
            "Root cause Gemini output failed validation: %s",
            type(error).__name__,
        )
        return None, "validation_failed", "AI output failed evidence validation."
    audit.succeed(report.model_dump(mode="json"))
    return report, "success", None


def _merge_data_quality(
    datasets: list[ManufacturingDataset],
) -> dict[str, Any]:
    summaries = [dataset.data_quality.model_dump(mode="json") for dataset in datasets]
    additive_fields = (
        "rows_read",
        "empty_rows",
        "duplicate_rows",
        "valid_rows",
        "invalid_rows",
        "rejected_rows",
        "rows_used_for_analysis",
    )
    merged: dict[str, Any] = {
        field: sum(summary[field] for summary in summaries)
        for field in additive_fields
    }
    merged["missing_values"] = {}
    merged["invalid_values"] = {}
    merged["warnings"] = []
    for summary in summaries:
        for output_key in ("missing_values", "invalid_values"):
            for name, count in summary[output_key].items():
                merged[output_key][name] = (
                    merged[output_key].get(name, 0) + count
                )
        for warning in summary["warnings"]:
            if warning not in merged["warnings"] and len(merged["warnings"]) < 20:
                merged["warnings"].append(warning)
    denominator = merged["rows_read"] - merged["empty_rows"]
    merged["quality_score"] = (
        round(merged["valid_rows"] / denominator * 100, 1)
        if denominator > 0
        else 0.0
    )
    merged["quality_status"] = (
        "good"
        if merged["quality_score"] >= 95
        else "warning"
        if merged["quality_score"] >= 80
        else "poor"
    )
    merged["scoring_basis"] = (
        "Combined valid rows divided by non-empty rows across uploaded files."
    )
    return merged


def analyze_root_cause_intelligence(
    dataframe: pd.DataFrame | None = None,
    *,
    problem_statement: str,
    production_dataframe: pd.DataFrame | None = None,
    quality_dataframe: pd.DataFrame | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    column_mapping: dict[str, str] | None = None,
    production_column_mapping: dict[str, str] | None = None,
    quality_column_mapping: dict[str, str] | None = None,
    time_bucket: Literal["day", "week", "month"] = "day",
    group_dimensions: list[str] | None = None,
    outcome_metric: str = "defect_rate",
    include_ai: bool = True,
) -> dict[str, Any]:
    normalized_problem = problem_statement.strip()
    if not normalized_problem:
        raise ValueError("Problem statement must not be empty.")
    if len(normalized_problem) > 4000:
        raise ValueError("Problem statement must be at most 4000 characters.")
    if time_bucket not in {"day", "week", "month"}:
        raise ValueError("Time bucket must be day, week, or month.")
    selected_dimensions = list(dict.fromkeys(group_dimensions or []))
    if any(dimension not in _DIMENSIONS for dimension in selected_dimensions):
        raise ValueError("Unsupported root-cause grouping dimension.")
    metric_key = _VARIABLE_ALIASES.get(outcome_metric)
    if metric_key is None:
        raise ValueError("Unsupported root-cause outcome metric.")

    quality_targets = {
        "Inspected Quantity",
        "Defective Units",
        "Defects",
        "Measurement Value",
        "Quality Status",
        "Defect Type",
    }
    production_targets = {
        "Production",
        "Planned Production",
        "Downtime",
        "Cycle Time",
        "Operating Time",
        "Waiting Time",
    }
    split_inputs = (
        production_dataframe is not None or quality_dataframe is not None
    )
    if dataframe is not None and split_inputs:
        raise ValueError(
            "Upload either one combined file or both production and quality files, not both."
        )
    if split_inputs and (
        production_dataframe is None or quality_dataframe is None
    ):
        raise ValueError(
            "Both production and quality files are required for split input."
        )
    input_frames = (
        [
            (
                production_dataframe,
                production_column_mapping,
            ),
            (
                quality_dataframe,
                quality_column_mapping,
            ),
        ]
        if split_inputs
        else [(dataframe, column_mapping)]
    )
    if any(frame is None for frame, _ in input_frames):
        raise ValueError("Provide a combined file or both split input files.")
    normalized_datasets: list[ManufacturingDataset] = []
    for frame, mapping in input_frames:
        assert frame is not None
        normalized_datasets.append(
            normalize_manufacturing_dataframe(
                frame,
                column_mapping=mapping,
                source_timezone=source_timezone,
                downtime_unit=downtime_unit,
                cycle_time_unit=cycle_time_unit,
                duration_unit=duration_unit,
                production_unit=production_unit,
                require_production=False,
            )
        )
    if split_inputs:
        production_targets = set(
            normalized_datasets[0].column_mapping.values()
        )
        quality_input_targets = set(
            normalized_datasets[1].column_mapping.values()
        )
        if "Production" not in production_targets:
            raise ValueError("The production file must include a Production field.")
        if not quality_input_targets.intersection(quality_targets):
            raise ValueError("The quality file must include a quality measurement.")
    records = [
        record
        for dataset in normalized_datasets
        for record in dataset.records
    ]
    mapped_targets = {
        target
        for dataset in normalized_datasets
        for target in dataset.column_mapping.values()
    }
    data_quality = _merge_data_quality(normalized_datasets)
    if not mapped_targets.intersection(quality_targets | production_targets):
        raise ValueError(
            "Upload must include at least one production or quality measurement."
        )

    if split_inputs:
        production_records = normalized_datasets[0].records
        quality_records = normalized_datasets[1].records
        production_record_ids = {
            id(record) for record in production_records
        }
        quality_record_ids = {
            id(record)
            for record in quality_records
            if any(
                value is not None
                for value in (
                    record.inspected_quantity,
                    record.defective_units,
                    record.defects,
                    record.defect_type,
                    record.quality_status,
                    record.measurement_value,
                    record.first_pass_inspected,
                    record.first_pass_passed,
                )
            )
        }
    else:
        production_record_ids = (
            {id(record) for record in records}
            if "Production" in mapped_targets
            else set()
        )
        quality_record_ids = {
            id(record)
            for record in records
            if any(
                value is not None
                for value in (
                    record.inspected_quantity,
                    record.defective_units,
                    record.defects,
                    record.defect_type,
                    record.quality_status,
                    record.measurement_value,
                    record.first_pass_inspected,
                    record.first_pass_passed,
                )
            )
        }
    observations = _group_records(
        records,
        time_bucket=time_bucket,
        dimensions=selected_dimensions,
        mapped_targets=mapped_targets,
        source_timezone=source_timezone,
        production_record_ids=production_record_ids,
        quality_record_ids=quality_record_ids,
        production_targets=production_targets,
        quality_targets=quality_targets,
    )
    evidence: list[dict[str, Any]] = []
    evidence_by_group: list[str | None] = []
    for item in observations:
        evidence_by_group.append(
            _add_evidence(
                evidence,
                metric="linked_production_quality_observation",
                value={
                    name: _rounded(value)
                    for name, value in item["metrics"].items()
                    if value is not None
                },
                method="normalized_records_aggregated_by_time_and_selected_dimensions",
                record_count=item["record_count"],
                dimensions=item["scope"],
                unit="mixed",
            )
            if len(evidence) < _MAX_EVIDENCE
            else None
        )
        item.pop("records")

    correlation_matrix, notable_correlations = _correlations(
        observations,
        evidence,
        evidence_by_group,
    )
    fishbone, factor_findings = _fishbone(
        records,
        mapped_targets,
        production_record_ids,
        evidence,
    )
    overall_inspected, overall_defects, _ = _defect_counts(records)
    overall_defect_rate = (
        overall_defects / overall_inspected * 100
        if overall_inspected is not None
        and overall_defects is not None
        and overall_inspected > 0
        else None
    )
    hypotheses = _rank_hypotheses(
        notable_correlations,
        factor_findings,
        outcome_metric=metric_key,
        overall_defect_rate=overall_defect_rate,
    )
    if not hypotheses:
        missing_data = [
            name
            for name in (
                "Inspected Quantity",
                "Defective Units or Defects",
                "Date",
                "Product",
                "Process",
                "Shift",
                "Line",
            )
            if name not in mapped_targets
        ]
        hypotheses.append(
            {
                "id": "H001",
                "hypothesis": "There is not enough linked evidence to rank a likely contributing factor.",
                "factor": None,
                "factor_level": None,
                "outcome_metric": metric_key,
                "direction": "unknown",
                "support_score": 0,
                "support_basis": "insufficient paired observations",
                "sample_count": 0,
                "evidence_ids": [],
                "confirming_test": "Collect aligned production and quality records, then repeat the analysis.",
                "missing_data": missing_data,
                "causality_status": "unranked",
            }
        )
    corrective_actions = [
        _action_for_candidate(candidate)
        for candidate in hypotheses
        if candidate["factor"] is not None
    ]
    for category in fishbone:
        for factor in category["factor_fields"]:
            if "levels" in factor:
                factor["levels"] = factor["levels"][:5]

    available_targets = sorted(mapped_targets)
    limitations = [
        "Tương quan và chênh lệch giữa các nhóm không chứng minh quan hệ nhân quả.",
        "Bản ghi chỉ được liên kết theo khoảng thời gian và dimensions đã chọn; không thể ghép khóa không được cung cấp.",
        "Không nội suy mẫu số bị thiếu hoặc không nhất quán.",
        "Điểm hỗ trợ yếu tố là heuristic minh bạch, không phải xác suất hay khoảng tin cậy.",
    ]
    if "Date" not in mapped_targets or not any(
        record.timestamp is not None for record in records
    ):
        limitations.append(
            "Không có timestamp hợp lệ; không thể phân tích theo thời gian."
        )
    if "Production" not in mapped_targets:
        limitations.append("Không ánh xạ được Production; không thể phân tích tương quan sản xuất.")
    if not mapped_targets.intersection(quality_targets):
        limitations.append("Không ánh xạ được chỉ số chất lượng; không thể phân tích defect rate.")
    if "Defective Units" in mapped_targets and "Inspected Quantity" not in mapped_targets:
        limitations.append("Không thể tính defect rate từ Defective Units nếu thiếu Inspected Quantity.")
    if correlation_matrix["omitted_variable_count"]:
        limitations.append(
            f"Ma trận tương quan giới hạn ở {_MAX_CORRELATION_VARIABLES} biến; "
            f"đã bỏ {correlation_matrix['omitted_variable_count']} biến bổ sung."
        )
    linked_observation_count = sum(
        item["has_production"] and item["has_quality"]
        for item in observations
    )
    if split_inputs and linked_observation_count == 0:
        limitations.append(
            "Không tìm thấy nhóm nào khớp giữa hai file theo thời gian và dimensions đã chọn."
        )

    baseline_value = None
    baseline_count = 0
    if metric_key == "defect_rate":
        baseline_value = overall_defect_rate
        baseline_count = sum(
            record.inspected_quantity is not None for record in records
        )
    else:
        values = [
            observation["metrics"].get(metric_key)
            for observation in observations
            if observation["metrics"].get(metric_key) is not None
        ]
        if values:
            baseline_value = statistics.fmean(values)
            baseline_count = len(values)
    baseline_metrics = (
        {
            metric_key: {
                "value": _rounded(baseline_value),
                "sample_count": max(1, baseline_count),
            }
        }
        if baseline_value is not None and baseline_count > 0
        else {}
    )

    five_whys = {
        "status": "guided_investigation",
        "steps": [
            {
                "why_number": number,
                "question": question,
                "answer": None,
                "evidence_ids": (
                    hypotheses[0]["evidence_ids"] if number == 1 else []
                ),
            }
            for number, question in enumerate(
                (
                    "Điều kiện nào đã được xác minh xuất hiện trực tiếp trước vấn đề?",
                    "Bằng chứng nào giải thích vì sao điều kiện đó xuất hiện?",
                    "Quy trình hoặc kiểm soát nào đã cho phép điều kiện đó xảy ra?",
                    "Vì sao kiểm soát hiện tại không ngăn ngừa hoặc phát hiện được?",
                    "Thay đổi hệ thống nào có thể ngăn tái diễn và sẽ xác minh ra sao?",
                ),
                start=1,
            )
        ],
        "notice": "Câu trả lời còn để mở cho đến khi điều tra viên xác minh bằng bằng chứng.",
    }
    result: dict[str, Any] = {
        "schema_version": "2.3",
        "problem_statement": normalized_problem,
        "data_scope": {
            "rows_read": data_quality["rows_read"],
            "rows_used": data_quality["rows_used_for_analysis"],
            "linked_observation_count": linked_observation_count,
            "unlinked_observation_count": len(observations) - linked_observation_count,
            "time_bucket": time_bucket,
            "group_dimensions": selected_dimensions,
            "source_timezone": source_timezone,
            "stored_timezone": "UTC",
            "mapped_fields": available_targets,
            "input_mode": "split_files" if split_inputs else "combined_file",
            "data_quality": data_quality,
        },
        "correlation_matrix": correlation_matrix,
        "notable_correlations": notable_correlations,
        "fishbone_analysis": fishbone,
        "five_whys": five_whys,
        "root_cause_candidates": hypotheses,
        "hypothesis_score_basis": (
            "Điểm tương quan là trị tuyệt đối Pearson/Spearman × 100; điểm phân tầng "
            "là heuristic mức tăng tương đối có giới hạn, không phải xác suất."
        ),
        "corrective_actions": corrective_actions,
        "baseline_metrics": baseline_metrics,
        "confidence_assessment": {
            "level": (
                "limited"
                if len(observations) < _MIN_CORRELATION_SAMPLES
                else "moderate"
                if hypotheses[0]["support_score"] < 70
                else "strong_association_evidence"
            ),
            "basis": (
                "Chỉ mô tả độ bao phủ evidence và mức liên hệ; không phải xác suất hay độ tin cậy nhân quả."
            ),
            "paired_observation_count": len(observations),
            "causality_established": False,
        },
        "verification_plan": (
            "Lặp lại tín hiệu trên các nhóm khớp theo sản phẩm/công đoạn/ca, "
            "xác minh độ bao phủ đo lường và kiểm tra, sau đó thử hành động có "
            "kiểm soát hoặc theo giai đoạn và so sánh cùng KPI trước/sau."
        ),
        "analysis_limitations": limitations,
        "evidence_references": evidence,
        "ai_status": "not_requested",
        "ai_error": None,
        "ai_report": None,
    }
    if include_ai:
        ai_report, ai_status, ai_error = _interpret_with_gemini(result)
        result["ai_status"] = ai_status
        result["ai_error"] = ai_error
        if ai_report is not None:
            result["ai_report"] = ai_report.model_dump(mode="json")
            result["five_whys"] = {
                "status": "hypotheses_require_verification",
                "steps": [
                    {
                        "why_number": number,
                        "question": f"Why did this condition occur at this level?",
                        "answer": statement.text,
                        "evidence_ids": statement.evidence_ids,
                    }
                    for number, statement in enumerate(ai_report.five_whys, start=1)
                ],
                "notice": (
                    "Câu trả lời do AI tạo là giả thuyết có dẫn chứng, chưa phải nguyên nhân đã xác minh."
                ),
            }
    return result
