from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    ManufacturingRecord,
)


_METRICS = (
    ("production", "production", "production"),
    ("defects", "defects", "count"),
    ("downtime_minutes", "downtime", "minutes"),
)
_ANOMALY_MIN_BASELINE_PERIODS = 8
_BOTTLENECK_MIN_RECORDS = 3


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


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


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_deviation = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
    )
    right_deviation = math.sqrt(
        sum((value - right_mean) ** 2 for value in right)
    )
    if left_deviation == 0 or right_deviation == 0:
        return None
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    return max(-1.0, min(1.0, covariance / (left_deviation * right_deviation)))


def _daily_series(
    records: list[ManufacturingRecord],
    available_fields: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[date, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if record.timestamp is not None:
            grouped[record.timestamp.date()].append(record)

    periods: list[dict[str, Any]] = []
    for period, period_records in sorted(grouped.items()):
        production = (
            sum(record.production for record in period_records)
            if "Production" in available_fields
            else None
        )
        defect_records = [
            record for record in period_records if record.defects is not None
        ]
        downtime_records = [
            record
            for record in period_records
            if record.downtime_minutes is not None
        ]
        paired = [
            record
            for record in period_records
            if (
                "Production" in available_fields
                and "Defects" in available_fields
                and record.defects is not None
                and record.production > 0
            )
        ]
        paired_production = sum(record.production for record in paired)
        paired_defects = sum(record.defects or 0 for record in paired)
        periods.append(
            {
                "period_start": period.isoformat(),
                "production": _rounded(production),
                "defects": (
                    _rounded(
                        sum(record.defects or 0 for record in defect_records)
                    )
                    if "Defects" in available_fields
                    else None
                ),
                "defect_rate": (
                    _rounded(paired_defects / paired_production * 100)
                    if paired_production > 0
                    else None
                ),
                "downtime_minutes": (
                    _rounded(
                        sum(
                            record.downtime_minutes or 0
                            for record in downtime_records
                        )
                    )
                    if "Downtime" in available_fields
                    else None
                ),
                "record_count": len(period_records),
            }
        )
    return periods


def _metric_values(
    records: list[ManufacturingRecord],
    metric: str,
) -> list[float]:
    values = [getattr(record, metric) for record in records]
    return [
        float(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]


def analyze_manufacturing_dataset(
    dataset: ManufacturingDataset,
    *,
    modules: list[str] | None = None,
) -> dict[str, Any]:
    records = dataset.records
    available_fields = set(dataset.column_mapping.values())
    active_modules = set(
        modules
        if modules is not None
        else (
            "data_understanding",
            "validation",
            "kpi_calculation",
            "statistical_analysis",
            "trend_analysis",
            "correlation_analysis",
            "anomaly_detection",
            "bottleneck_screening",
            "evidence_generation",
        )
    )
    evidence: list[dict[str, Any]] = []
    evidence_by_metric: dict[str, str] = {}

    def add_evidence(
        *,
        metric: str,
        value: Any,
        unit: str,
        method: str,
        record_count: int,
        period: str | None = None,
        dimensions: dict[str, str] | None = None,
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
        if period is not None:
            item["period"] = period
        if dimensions:
            item["dimensions"] = dimensions
        evidence.append(item)
        return evidence_id

    metric_totals: dict[str, dict[str, Any]] = {}
    for record_field, metric_name, unit in _METRICS:
        field_available = {
            "production": "Production",
            "defects": "Defects",
            "downtime_minutes": "Downtime",
        }[record_field] in available_fields
        values = (
            _metric_values(records, record_field)
            if field_available
            else []
        )
        value = _rounded(sum(values)) if values else None
        evidence_id = (
            add_evidence(
                metric=metric_name,
                value=value,
                unit=dataset.units.get(metric_name, unit),
                method="sum_of_valid_records",
                record_count=len(values),
            )
            if value is not None
            else None
        )
        if evidence_id:
            evidence_by_metric[metric_name] = evidence_id
        metric_totals[metric_name] = {
            "value": value,
            "unit": dataset.units.get(metric_name, unit),
            "record_count": len(values),
            "evidence_id": evidence_id,
        }

    paired_records = [
        record
        for record in records
        if record.defects is not None and record.production > 0
    ]
    paired_production = sum(record.production for record in paired_records)
    paired_defects = sum(record.defects or 0 for record in paired_records)
    defect_rate = (
        _rounded(paired_defects / paired_production * 100)
        if paired_production > 0
        else None
    )
    defect_rate_evidence_id = (
        add_evidence(
            metric="defect_rate",
            value=defect_rate,
            unit="percent",
            method="paired_defects_divided_by_paired_production",
            record_count=len(paired_records),
        )
        if defect_rate is not None
        else None
    )
    metric_totals["defect_rate"] = {
        "value": defect_rate,
        "unit": "percent",
        "record_count": len(paired_records),
        "evidence_id": defect_rate_evidence_id,
    }

    metric_totals["record_count"] = {
        "value": len(records),
        "unit": "records",
        "record_count": len(records),
        "evidence_id": add_evidence(
            metric="record_count",
            value=len(records),
            unit="records",
            method="count_of_unique_usable_records",
            record_count=len(records),
        ),
    }

    statistics_findings: list[dict[str, Any]] = []
    if "statistical_analysis" in active_modules:
        for record_field, metric_name, unit in _METRICS:
            target_field = {
                "production": "Production",
                "defects": "Defects",
                "downtime_minutes": "Downtime",
            }[record_field]
            if target_field not in available_fields:
                continue
            values = _metric_values(records, record_field)
            if not values:
                continue
            summary = {
                "count": len(values),
                "mean": _rounded(statistics.fmean(values)),
                "median": _rounded(statistics.median(values)),
                "standard_deviation": _rounded(statistics.pstdev(values)),
                "minimum": _rounded(min(values)),
                "maximum": _rounded(max(values)),
            }
            evidence_id = add_evidence(
                metric=f"{metric_name}_statistics",
                value=summary,
                unit=dataset.units.get(metric_name, unit),
                method="descriptive_statistics",
                record_count=len(values),
            )
            statistics_findings.append(
                {
                    "type": "descriptive_statistics",
                    "metric": metric_name,
                    **summary,
                    "unit": dataset.units.get(metric_name, unit),
                    "evidence_ids": [evidence_id],
                }
            )

    correlations: list[dict[str, Any]] = []
    if "correlation_analysis" in active_modules:
        for left_index, (left_name, left_field) in enumerate(
            (("production", "production"), ("defects", "defects"), ("downtime", "downtime_minutes"))
        ):
            for right_name, right_field in (
                ("production", "production"),
                ("defects", "defects"),
                ("downtime", "downtime_minutes"),
            )[left_index + 1 :]:
                canonical_fields = {
                    "production": "Production",
                    "defects": "Defects",
                    "downtime": "Downtime",
                }
                if (
                    canonical_fields[left_name] not in available_fields
                    or canonical_fields[right_name] not in available_fields
                ):
                    continue
                pairs = [
                    (float(getattr(record, left_field)), float(getattr(record, right_field)))
                    for record in records
                    if getattr(record, left_field) is not None
                    and getattr(record, right_field) is not None
                ]
                coefficient = _correlation(
                    [pair[0] for pair in pairs],
                    [pair[1] for pair in pairs],
                )
                if coefficient is None:
                    continue
                rounded_coefficient = _rounded(coefficient)
                evidence_id = add_evidence(
                    metric=f"correlation:{left_name}:{right_name}",
                    value=rounded_coefficient,
                    unit="pearson_r",
                    method="pearson_correlation",
                    record_count=len(pairs),
                )
                correlations.append(
                    {
                        "type": "correlation",
                        "metrics": [left_name, right_name],
                        "coefficient": rounded_coefficient,
                        "paired_record_count": len(pairs),
                        "interpretation": (
                            "association_only_not_causation"
                        ),
                        "evidence_ids": [evidence_id],
                    }
                )
    statistics_findings.extend(correlations)

    time_series = _daily_series(records, available_fields)
    detected_anomalies: list[dict[str, Any]] = []
    if "anomaly_detection" in active_modules:
        for metric_name in ("production", "defects", "defect_rate", "downtime_minutes"):
            valid_periods = [
                period
                for period in time_series
                if isinstance(period.get(metric_name), (int, float))
            ]
            if len(valid_periods) <= _ANOMALY_MIN_BASELINE_PERIODS:
                continue
            baseline = [
                float(period[metric_name])
                for period in valid_periods[:-1]
            ]
            observed = float(valid_periods[-1][metric_name])
            first_quartile = _quantile(baseline, 0.25)
            third_quartile = _quantile(baseline, 0.75)
            spread = third_quartile - first_quartile
            lower_bound = first_quartile - 1.5 * spread
            upper_bound = third_quartile + 1.5 * spread
            if lower_bound <= observed <= upper_bound:
                continue
            unit = (
                dataset.units["defect_rate"]
                if metric_name == "defect_rate"
                else dataset.units["downtime"]
                if metric_name == "downtime_minutes"
                else dataset.units["production"]
                if metric_name == "production"
                else dataset.units["defects"]
            )
            evidence_id = add_evidence(
                metric=metric_name,
                value={
                    "observed": _rounded(observed),
                    "lower_bound": _rounded(lower_bound),
                    "upper_bound": _rounded(upper_bound),
                    "baseline_periods": len(baseline),
                },
                unit=unit,
                method="historical_iqr_1_5",
                record_count=len(valid_periods),
                period=str(valid_periods[-1]["period_start"]),
            )
            detected_anomalies.append(
                {
                    "type": "historical_iqr_outlier",
                    "metric": metric_name,
                    "period": valid_periods[-1]["period_start"],
                    "observed": _rounded(observed),
                    "lower_bound": _rounded(lower_bound),
                    "upper_bound": _rounded(upper_bound),
                    "baseline_periods": len(baseline),
                    "unit": unit,
                    "evidence_ids": [evidence_id],
                }
            )

    bottleneck_candidates: list[dict[str, Any]] = []
    if (
        "bottleneck_screening" in active_modules
        and {"Production", "Downtime"}.issubset(available_fields)
    ):
        for dimension in ("line", "machine", "process"):
            grouped: dict[str, list[ManufacturingRecord]] = defaultdict(list)
            for record in records:
                label = getattr(record, dimension)
                if label:
                    grouped[label].append(record)
            candidates: list[tuple[float, str, list[ManufacturingRecord]]] = []
            for label, grouped_records in grouped.items():
                production = sum(record.production for record in grouped_records)
                downtime = sum(
                    record.downtime_minutes or 0
                    for record in grouped_records
                    if record.downtime_minutes is not None
                )
                if (
                    len(grouped_records) < _BOTTLENECK_MIN_RECORDS
                    or production <= 0
                    or not any(
                        record.downtime_minutes is not None
                        for record in grouped_records
                    )
                ):
                    continue
                candidates.append((downtime / production, label, grouped_records))
            if not candidates:
                continue
            ratio, label, grouped_records = max(candidates, key=lambda item: item[0])
            evidence_id = add_evidence(
                metric="downtime_per_production_unit",
                value=_rounded(ratio),
                unit="minutes_per_production_unit",
                method="group_downtime_divided_by_group_production",
                record_count=len(grouped_records),
                dimensions={dimension: label},
            )
            bottleneck_candidates.append(
                {
                    "type": "potential_bottleneck",
                    "dimension": dimension,
                    "entity": label,
                    "downtime_per_production_unit": _rounded(ratio),
                    "record_count": len(grouped_records),
                    "evidence_ids": [evidence_id],
                    "interpretation": (
                        "screening_signal_not_confirmed_capacity_constraint"
                    ),
                }
            )

    recommendations = [
        {
            "action": (
                f"Kiểm tra quy trình tại kỳ {anomaly['period']} để xác minh "
                f"biến động {anomaly['metric']}."
            ),
            "reason": "Anomaly thống kê cần được xác minh tại nguồn.",
            "evidence_ids": anomaly["evidence_ids"],
            "priority": "medium",
        }
        for anomaly in detected_anomalies
    ]
    recommendations.extend(
        {
            "action": (
                f"Thu thập cycle time và lịch sử dừng của {candidate['dimension']} "
                f"{candidate['entity']} trước khi kết luận về bottleneck."
            ),
            "reason": (
                "Tỷ lệ downtime trên sản lượng là tín hiệu sàng lọc, "
                "không chứng minh giới hạn công suất."
            ),
            "evidence_ids": candidate["evidence_ids"],
            "priority": "low",
        }
        for candidate in bottleneck_candidates
    )

    quality = dataset.data_quality
    sample_factor = min(1.0, len(records) / 30)
    confidence_score = round(quality.quality_score * sample_factor, 1)
    confidence_level = (
        "high"
        if confidence_score >= 85 and len(records) >= 30
        else "medium"
        if confidence_score >= 65 and len(records) >= 10
        else "low"
    )
    limitations = []
    if "Production" not in available_fields:
        limitations.append(
            "Không có cột Production; KPI sản lượng và tỷ lệ lỗi không được tính."
        )
    if (
        active_modules.intersection({"trend_analysis", "anomaly_detection"})
        and not any(record.timestamp is not None for record in records)
    ):
        limitations.append(
            "Thiếu timestamp hợp lệ nên không thể phân tích xu hướng theo thời gian."
        )
    if not any(record.defects is not None for record in records):
        limitations.append("Không có dữ liệu Defects để tính tỷ lệ lỗi.")
    if not any(record.downtime_minutes is not None for record in records):
        limitations.append("Không có dữ liệu Downtime để sàng lọc bottleneck.")
    if (
        "anomaly_detection" in active_modules
        and len(time_series) <= _ANOMALY_MIN_BASELINE_PERIODS
    ):
        limitations.append(
            "Cần ít nhất 9 kỳ ngày có dữ liệu để phát hiện outlier dựa trên baseline."
        )
    if correlations:
        limitations.append(
            "Tương quan mô tả mối liên hệ, không chứng minh quan hệ nhân quả."
        )
    if bottleneck_candidates:
        limitations.append(
            "Sàng lọc bottleneck chỉ dựa trên downtime/sản lượng; cần cycle time, "
            "công suất và lịch vận hành để xác nhận."
        )
    if quality.warnings:
        limitations.extend(quality.warnings)

    total_production = metric_totals["production"]["value"]
    executive_summary = (
        f"Đã phân tích {len(records)} bản ghi; "
        f"tổng sản lượng là {total_production:g} "
        f"{dataset.units['production']}."
        if total_production is not None
        else f"Đã phân tích {len(records)} bản ghi."
    )
    if detected_anomalies:
        executive_summary += (
            f" Phát hiện {len(detected_anomalies)} tín hiệu outlier cần xác minh."
        )

    return {
        "executive_summary": executive_summary,
        "data_quality": quality.model_dump(mode="json"),
        "kpi_results": metric_totals,
        "statistical_findings": statistics_findings,
        "time_series": (
            time_series if "trend_analysis" in active_modules else []
        ),
        "detected_anomalies": detected_anomalies,
        "potential_bottlenecks": bottleneck_candidates,
        "root_cause_hypotheses": [],
        "recommendations": recommendations,
        "confidence_assessment": {
            "score": confidence_score,
            "level": confidence_level,
            "is_probability": False,
            "method": "data_quality_score_times_sample_size_factor",
            "rationale": (
                "Chỉ số heuristic về độ đầy đủ và quy mô mẫu; "
                "không phải xác suất đúng."
            ),
        },
        "evidence_references": evidence,
        "analysis_limitations": list(dict.fromkeys(limitations)),
        "executed_modules": modules
        or [
            "data_understanding",
            "validation",
            "kpi_calculation",
            "statistical_analysis",
            "trend_analysis",
            "correlation_analysis",
            "anomaly_detection",
            "bottleneck_screening",
            "evidence_generation",
        ],
    }
