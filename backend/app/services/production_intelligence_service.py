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
from app.services.manufacturing_analytics_engine import (
    analyze_manufacturing_dataset,
)
from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    ManufacturingRecord,
    normalize_manufacturing_dataframe,
)


logger = logging.getLogger(__name__)
_MIN_ANOMALY_BASELINE = 8
_AI_ANSWER_TOPICS = {
    "weakest_line",
    "plan_shortfall",
    "best_shift",
    "bottleneck_process",
    "production_decline",
}


class ProductionAIStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class ProductionAIAnswer(ProductionAIStatement):
    topic: Literal[
        "weakest_line",
        "plan_shortfall",
        "best_shift",
        "bottleneck_process",
        "production_decline",
    ]


class ProductionAIInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: ProductionAIStatement
    answers: list[ProductionAIAnswer] = Field(max_length=5)
    root_cause_hypotheses: list[ProductionAIStatement] = Field(max_length=10)
    recommendations: list[ProductionAIStatement] = Field(max_length=10)
    limitations: list[str] = Field(max_length=10)


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _elapsed_minutes(record: ManufacturingRecord) -> float | None:
    start = record.production_start
    end = record.production_end
    if start is None or end is None:
        return None
    duration = end - start
    if duration.total_seconds() < 0 and start.date() == end.date():
        duration += timedelta(days=1)
    minutes = duration.total_seconds() / 60
    return minutes if minutes > 0 else None


def _time_minutes(
    records: list[ManufacturingRecord],
    field: Literal[
        "available_time_minutes",
        "operating_time_minutes",
        "waiting_time_minutes",
    ],
    *,
    allow_elapsed_fallback: bool = False,
) -> tuple[float | None, str | None]:
    explicit: list[float] = []
    seen_buckets: set[tuple[Any, ...]] = set()
    for record in records:
        value = getattr(record, field)
        if value is None:
            continue
        date = record.timestamp.date() if record.timestamp else None
        bucket = (
            record.line,
            record.machine,
            record.shift,
            date,
            record.production_start,
            record.production_end,
            float(value),
        )
        if not (record.line or record.machine) or not (
            date or record.production_start or record.production_end
        ):
            bucket = (*bucket, record.source_row)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        explicit.append(float(value))
    if explicit:
        return sum(explicit), field
    if allow_elapsed_fallback:
        elapsed: list[float] = []
        seen_intervals: set[tuple[Any, ...]] = set()
        for record in records:
            duration = _elapsed_minutes(record)
            if duration is None:
                continue
            interval_key = (
                record.line,
                record.shift,
                record.machine,
                record.timestamp.date() if record.timestamp else None,
                record.production_start,
                record.production_end,
            )
            if not (record.line or record.machine):
                interval_key = (*interval_key, record.source_row)
            if interval_key in seen_intervals:
                continue
            seen_intervals.add(interval_key)
            elapsed.append(duration)
        if elapsed:
            return sum(elapsed), "production_start_end_elapsed"
    return None, None


def _metric(
    value: float | None,
    unit: str,
    basis: str,
    record_count: int,
) -> dict[str, Any]:
    return {
        "value": _rounded(value),
        "unit": unit,
        "basis": basis,
        "record_count": record_count,
    }


def _group_summary(
    records: list[ManufacturingRecord],
    *,
    label: str,
    name: str,
    production_unit: str,
) -> dict[str, Any]:
    actual = sum(record.production for record in records)
    planned_records = [
        record for record in records if record.planned_production is not None
    ]
    planned = (
        sum(record.planned_production or 0 for record in planned_records)
        if planned_records
        else None
    )
    actual_on_planned_rows = (
        sum(record.production for record in planned_records)
        if planned_records
        else None
    )
    operating_explicit_minutes, operating_basis = _time_minutes(
        records,
        "operating_time_minutes",
    )
    operating_elapsed_minutes, elapsed_operating_basis = _time_minutes(
        records,
        "operating_time_minutes",
        allow_elapsed_fallback=True,
    )
    operating_minutes = (
        operating_explicit_minutes
        if operating_explicit_minutes is not None
        else operating_elapsed_minutes
    )
    if operating_explicit_minutes is None:
        operating_basis = elapsed_operating_basis
    available_explicit_minutes, available_basis = _time_minutes(
        records,
        "available_time_minutes",
    )
    elapsed_minutes, elapsed_basis = _time_minutes(
        records,
        "available_time_minutes",
        allow_elapsed_fallback=True,
    )
    available_minutes = (
        available_explicit_minutes
        if available_explicit_minutes is not None
        else elapsed_minutes
    )
    if available_explicit_minutes is None:
        available_basis = elapsed_basis
    operating_records = [
        record
        for record in records
        if record.operating_time_minutes is not None
    ]
    if not operating_records and operating_elapsed_minutes is not None:
        operating_records = [
            record
            for record in records
            if _elapsed_minutes(record) is not None
        ]
    available_records = [
        record
        for record in records
        if record.available_time_minutes is not None
    ]
    if not available_records and elapsed_minutes is not None:
        available_records = [
            record
            for record in records
            if _elapsed_minutes(record) is not None
        ]
    utilization_records = [
        record
        for record in records
        if record.operating_time_minutes is not None
        and record.available_time_minutes is not None
    ]
    plan_time_records = [
        record
        for record in records
        if record.planned_production is not None
        and record.available_time_minutes is not None
    ]
    waiting_minutes, _ = _time_minutes(records, "waiting_time_minutes")
    plan_available_minutes, _ = _time_minutes(
        plan_time_records,
        "available_time_minutes",
    )
    utilization_operating_minutes, _ = _time_minutes(
        utilization_records,
        "operating_time_minutes",
    )
    utilization_available_minutes, _ = _time_minutes(
        utilization_records,
        "available_time_minutes",
    )
    cycle_records = [
        record
        for record in records
        if record.cycle_time_seconds is not None
    ]
    cycle_time = (
        sum(
            (record.cycle_time_seconds or 0) * record.production
            for record in cycle_records
        )
        / sum(record.production for record in cycle_records)
        if cycle_records
        and sum(record.production for record in cycle_records) > 0
        else None
    )
    operating_production = sum(record.production for record in operating_records)
    if (
        cycle_time is None
        and operating_minutes
        and operating_minutes > 0
        and operating_production > 0
    ):
        cycle_time = operating_minutes * 60 / operating_production

    result: dict[str, Any] = {
        "dimension": name,
        "entity": label,
        "actual_production": _rounded(actual),
        "production_unit": production_unit,
        "defects": _rounded(
            sum(record.defects or 0 for record in records)
            if any(record.defects is not None for record in records)
            else None
        ),
        "downtime_minutes": _rounded(
            sum(record.downtime_minutes or 0 for record in records)
            if any(record.downtime_minutes is not None for record in records)
            else None
        ),
        "record_count": len(records),
        "planned_production": _rounded(planned),
        "actual_on_planned_rows": _rounded(actual_on_planned_rows),
        "production_achievement_rate": (
            _rounded(actual_on_planned_rows / planned * 100)
            if planned is not None and planned > 0
            and actual_on_planned_rows is not None
            else None
        ),
        "defect_rate": (
            _rounded(
                sum(record.defects or 0 for record in records)
                / actual
                * 100
            )
            if actual > 0 and any(record.defects is not None for record in records)
            else None
        ),
        "plan_variance": (
            _rounded(actual_on_planned_rows - planned)
            if planned is not None and actual_on_planned_rows is not None
            else None
        ),
        "production_rate": (
            _rounded(
                sum(record.production for record in available_records)
                / (available_minutes / 60)
            )
            if available_minutes and available_minutes > 0
            else None
        ),
        "production_rate_basis": available_basis,
        "throughput": (
            _rounded(
                operating_production / (operating_minutes / 60)
            )
            if operating_minutes and operating_minutes > 0
            else None
        ),
        "throughput_basis": operating_basis,
        "cycle_time_seconds": _rounded(cycle_time),
        "cycle_time_basis": (
            "production_weighted_explicit_cycle_time"
            if cycle_records
            else (
                "operating_time_divided_by_actual_production"
                if operating_explicit_minutes is not None
                else "production_start_end_elapsed_divided_by_actual_production"
            )
            if cycle_time is not None
            else None
        ),
        "takt_time_seconds": (
            _rounded(
                plan_available_minutes * 60
                / sum(record.planned_production or 0 for record in plan_time_records)
            )
            if plan_time_records
            and plan_available_minutes is not None
            and sum(
                record.planned_production or 0 for record in plan_time_records
            )
            > 0
            else None
        ),
        "line_efficiency_percent": None,
        "line_utilization_percent": (
            _rounded(
                utilization_operating_minutes
                / utilization_available_minutes
                * 100
            )
            if utilization_records
            and utilization_operating_minutes is not None
            and utilization_available_minutes is not None
            and utilization_available_minutes > 0
            else None
        ),
        "available_time_minutes": _rounded(available_minutes),
        "operating_time_minutes": _rounded(operating_minutes),
        "waiting_time_minutes": (
            _rounded(waiting_minutes) if waiting_minutes is not None else None
        ),
    }

    standard_cycle_rows = [
        record
        for record in records
        if record.standard_cycle_time_seconds is not None
        and record.available_time_minutes is not None
    ]
    if standard_cycle_rows:
        available_standard_minutes, _ = _time_minutes(
            standard_cycle_rows,
            "available_time_minutes",
        )
        ideal_seconds = sum(
            record.production * (record.standard_cycle_time_seconds or 0)
            for record in standard_cycle_rows
        )
        if available_standard_minutes and available_standard_minutes > 0:
            result["line_efficiency_percent"] = _rounded(
                ideal_seconds / (available_standard_minutes * 60) * 100
            )
    return result


def _breakdown(
    records: list[ManufacturingRecord],
    dimension: str,
    *,
    production_unit: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        value = getattr(record, dimension)
        if value:
            grouped[value].append(record)
    result = [
        _group_summary(
            group,
            label=label,
            name=dimension,
            production_unit=production_unit,
        )
        for label, group in grouped.items()
    ]
    planned_rows = [
        row for row in result if row["production_achievement_rate"] is not None
    ]
    if planned_rows:
        return sorted(
            planned_rows,
            key=lambda item: item["production_achievement_rate"],
        ) + [
            row
            for row in result
            if row["production_achievement_rate"] is None
        ]
    throughput_rows = [row for row in result if row["throughput"] is not None]
    if throughput_rows:
        return sorted(throughput_rows, key=lambda item: item["throughput"]) + [
            row for row in result if row["throughput"] is None
        ]
    return sorted(
        result,
        key=lambda item: item["actual_production"],
    )


def _periods(
    records: list[ManufacturingRecord],
    granularity: Literal["day", "week", "month"],
    *,
    production_unit: str,
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
            monday = day - timedelta(days=day.weekday())
            key = monday.isoformat()
        else:
            key = f"{day.year:04d}-{day.month:02d}"
        grouped[key].append(record)
    result = [
        {
            "period": key,
            **_group_summary(
                rows,
                label=key,
                name=granularity,
                production_unit=production_unit,
            ),
        }
        for key, rows in sorted(grouped.items())
    ]
    return result


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


def _severity(shortfall_percent: float) -> str:
    if shortfall_percent >= 20:
        return "high"
    if shortfall_percent >= 10:
        return "medium"
    return "low"


def _detect_anomalies(
    daily: list[dict[str, Any]],
    records: list[ManufacturingRecord],
    evidence: list[dict[str, Any]],
    production_unit: str,
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for period in daily:
        planned = period["planned_production"]
        actual = period["actual_on_planned_rows"]
        if actual is None or planned is None or planned <= 0 or actual >= planned:
            continue
        shortfall = (planned - actual) / planned * 100
        evidence_id = _add_evidence(
            evidence,
            metric="plan_shortfall",
            value={
                "planned": planned,
                "actual": actual,
                "shortfall_percent": _rounded(shortfall),
            },
            unit=production_unit,
            method="planned_minus_actual",
            record_count=period["record_count"],
            period=period["period"],
        )
        anomalies.append(
            {
                "type": "plan_shortfall",
                "period": period["period"],
                "actual": actual,
                "planned": planned,
                "shortfall": _rounded(planned - actual),
                "shortfall_percent": _rounded(shortfall),
                "severity": _severity(shortfall),
                "method": "actual_below_explicit_plan",
                "evidence_ids": [evidence_id],
            }
        )

    if len(daily) >= _MIN_ANOMALY_BASELINE + 1:
        baseline = [
            float(item["actual_production"])
            for item in daily[-(_MIN_ANOMALY_BASELINE + 1) : -1]
        ]
        observed = float(daily[-1]["actual_production"])
        q1, q3 = (
            float(pd.Series(baseline).quantile(0.25)),
            float(pd.Series(baseline).quantile(0.75)),
        )
        lower_bound = q1 - 1.5 * (q3 - q1)
        if observed < lower_bound:
            reduction = (
                max(0.0, statistics.median(baseline) - observed)
                / statistics.median(baseline)
                * 100
                if statistics.median(baseline) > 0
                else 0
            )
            evidence_id = _add_evidence(
                evidence,
                metric="production_decline",
                value={
                    "observed": _rounded(observed),
                    "baseline_median": _rounded(statistics.median(baseline)),
                    "lower_iqr_bound": _rounded(lower_bound),
                    "baseline_periods": len(baseline),
                },
                unit=production_unit,
                method="historical_iqr_1_5",
                record_count=len(baseline) + 1,
                period=daily[-1]["period"],
            )
            anomalies.append(
                {
                    "type": "production_decline",
                    "period": daily[-1]["period"],
                    "observed": _rounded(observed),
                    "baseline_median": _rounded(statistics.median(baseline)),
                    "lower_bound": _rounded(lower_bound),
                    "reduction_percent": _rounded(reduction),
                    "severity": _severity(reduction),
                    "method": "historical_iqr_1_5",
                    "evidence_ids": [evidence_id],
                }
            )

    achievement_periods = [
        item
        for item in daily
        if item["production_achievement_rate"] is not None
    ]
    if len(achievement_periods) >= _MIN_ANOMALY_BASELINE + 1:
        baseline_periods = achievement_periods[-(_MIN_ANOMALY_BASELINE + 1) : -1]
        baseline = [
            float(item["production_achievement_rate"])
            for item in baseline_periods
        ]
        observed_period = achievement_periods[-1]
        observed = float(observed_period["production_achievement_rate"])
        q1 = float(pd.Series(baseline).quantile(0.25))
        q3 = float(pd.Series(baseline).quantile(0.75))
        lower_bound = q1 - 1.5 * (q3 - q1)
        if observed < lower_bound:
            baseline_median = statistics.median(baseline)
            reduction = (
                max(0, baseline_median - observed) / baseline_median * 100
                if baseline_median > 0
                else 0
            )
            evidence_id = _add_evidence(
                evidence,
                metric="production_achievement_decline",
                value={
                    "observed_percent": _rounded(observed),
                    "baseline_median_percent": _rounded(baseline_median),
                    "lower_iqr_bound_percent": _rounded(lower_bound),
                    "baseline_periods": len(baseline),
                },
                unit="percent",
                method="historical_iqr_1_5",
                record_count=len(baseline) + 1,
                period=observed_period["period"],
            )
            anomalies.append(
                {
                    "type": "production_achievement_decline",
                    "period": observed_period["period"],
                    "observed_percent": _rounded(observed),
                    "baseline_median_percent": _rounded(baseline_median),
                    "lower_bound_percent": _rounded(lower_bound),
                    "reduction_percent": _rounded(reduction),
                    "severity": _severity(reduction),
                    "method": "historical_iqr_1_5",
                    "evidence_ids": [evidence_id],
                }
            )

    cycle_groups: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if record.timestamp is not None and record.cycle_time_seconds is not None:
            cycle_groups[record.process or "unclassified"].append(record)
    for process, group in cycle_groups.items():
        timed = sorted(group, key=lambda record: record.timestamp)
        if len(timed) < _MIN_ANOMALY_BASELINE + 1:
            continue
        baseline_records = timed[-(_MIN_ANOMALY_BASELINE + 1) : -1]
        baseline = [
            float(record.cycle_time_seconds)
            for record in baseline_records
            if record.cycle_time_seconds is not None
        ]
        observed_record = timed[-1]
        observed = float(observed_record.cycle_time_seconds or 0)
        q1 = float(pd.Series(baseline).quantile(0.25))
        q3 = float(pd.Series(baseline).quantile(0.75))
        upper_bound = q3 + 1.5 * (q3 - q1)
        if observed <= upper_bound:
            continue
        baseline_median = statistics.median(baseline)
        increase = (
            (observed - baseline_median) / baseline_median * 100
            if baseline_median > 0
            else 100.0
        )
        dimensions = (
            {"process": process} if process != "unclassified" else None
        )
        evidence_id = _add_evidence(
            evidence,
            metric="cycle_time",
            value={
                "observed_seconds": _rounded(observed),
                "upper_iqr_bound_seconds": _rounded(upper_bound),
                "baseline_median_seconds": _rounded(baseline_median),
                "baseline_periods": len(baseline),
            },
            unit="seconds",
            method="historical_iqr_1_5",
            record_count=len(baseline) + 1,
            dimensions=dimensions,
            period=observed_record.timestamp.isoformat(),
        )
        anomalies.append(
            {
                "type": "cycle_time_increase",
                "period": observed_record.timestamp.isoformat(),
                "observed_seconds": _rounded(observed),
                "upper_bound_seconds": _rounded(upper_bound),
                "severity": _severity(increase),
                "increase_percent": _rounded(increase),
                "method": "historical_iqr_1_5",
                "evidence_ids": [evidence_id],
                "process": None if process == "unclassified" else process,
            }
        )
    return anomalies


def _validate_ai_interpretation(
    interpretation: ProductionAIInterpretation,
    evidence_ids: set[str],
) -> None:
    statements = [
        interpretation.executive_summary,
        *interpretation.answers,
        *interpretation.root_cause_hypotheses,
        *interpretation.recommendations,
    ]
    answer_topics = {answer.topic for answer in interpretation.answers}
    if answer_topics != _AI_ANSWER_TOPICS or len(interpretation.answers) != len(
        _AI_ANSWER_TOPICS
    ):
        raise ValueError("Production AI must answer every required question.")
    for statement in statements:
        if not set(statement.evidence_ids).issubset(evidence_ids):
            raise ValueError("Production AI referenced an unknown evidence ID.")
        if any(character.isdigit() for character in statement.text):
            raise ValueError(
                "Production AI must cite numeric results by evidence ID."
            )
    if any(
        any(character.isdigit() for character in limitation)
        for limitation in interpretation.limitations
    ):
        raise ValueError("Production AI limitation contains an unsupported number.")


def _interpret_with_gemini(
    analytics: dict[str, Any],
) -> tuple[ProductionAIInterpretation | None, str, str | None]:
    if not settings.GEMINI_API_KEY:
        return (
            None,
            "not_configured",
            "Gemini chưa được cấu hình; kết quả Python vẫn khả dụng.",
        )
    evidence_ids = {item["id"] for item in analytics["evidence_references"]}
    if not evidence_ids:
        return None, "insufficient_evidence", "Không có evidence sản xuất."

    from app.services.hardware_ai_service import (
        GEMINI_MODEL,
        _gemini_response_schema,
        get_client,
    )

    prompt_context = {
        "production_questions": [
            "Dây chuyền nào đang hoạt động kém nhất?",
            "Sản lượng đang thấp hơn kế hoạch bao nhiêu?",
            "Ca nào có hiệu suất tốt nhất?",
            "Công đoạn nào đang gây bottleneck?",
            "Những yếu tố nào có thể giải thích sự suy giảm?",
            "Cần cải thiện điều gì trước tiên?",
        ],
        "kpi_results": analytics["kpi_results"],
        "line_performance": analytics["line_performance"],
        "shift_performance": analytics["shift_performance"],
        "process_performance": analytics["process_performance"],
        "plan_vs_actual": analytics["plan_vs_actual"],
        "production_anomalies": analytics["production_anomalies"],
        "bottleneck_analysis": analytics["bottleneck_analysis"],
        "evidence_references": analytics["evidence_references"],
        "analysis_limitations": analytics["analysis_limitations"],
    }
    prompt = (
        "Trả lời bằng tiếng Việt cho người quản lý sản xuất. Chỉ diễn giải "
        "các số liệu và phạm vi đã được Python tính; không tự tính, không "
        "đưa số, ngày, ngưỡng, benchmark, nguyên nhân hoặc khuyến nghị vượt "
        "evidence. Phân biệt tương quan với nhân quả. Nếu thiếu dữ liệu, nói "
        "rõ là chưa đủ bằng chứng. Trả đủ đúng năm chủ đề answer: weakest_line, "
        "plan_shortfall, best_shift, bottleneck_process, production_decline; "
        "không viết chữ số trong text, dẫn số liệu qua evidence_ids. Hypotheses "
        "chỉ là giả thuyết. Recommendations phải ưu tiên hành động kiểm chứng "
        "có liên hệ evidence. JSON:\n"
        + json.dumps(prompt_context, ensure_ascii=False, allow_nan=False)
    )
    audit = AIAuditContext(
        analysis_type="production_intelligence",
        model_name=GEMINI_MODEL,
        prompt_version="production_intelligence_v1",
        input_data=prompt_context,
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
                        ProductionAIInterpretation
                    ),
                    "temperature": 0.2,
                },
            )
        )
    except Exception as error:
        audit.fail(error)
        logger.exception("Production intelligence Gemini request failed")
        return (
            None,
            "error",
            "Gemini không khả dụng; giữ nguyên kết quả sản xuất tính bằng Python.",
        )

    audit.set_usage_from_response(response)
    raw_response = getattr(response, "text", None)
    audit.set_raw_response(raw_response)
    try:
        if not raw_response or not raw_response.strip():
            raise ValueError("Gemini không trả về production interpretation.")
        interpretation = ProductionAIInterpretation.model_validate_json(
            raw_response
        )
        _validate_ai_interpretation(interpretation, evidence_ids)
    except Exception as error:
        audit.fail(error, validation_failed=True)
        logger.warning(
            "Production intelligence response failed validation: %s",
            type(error).__name__,
        )
        return (
            None,
            "validation_failed",
            "Diễn giải Gemini không đạt kiểm định evidence; kết quả Python vẫn dùng được.",
        )
    audit.succeed(interpretation.model_dump(mode="json"))
    return interpretation, "available", None


def analyze_production_intelligence(
    dataframe: pd.DataFrame,
    *,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    include_ai: bool = True,
) -> dict[str, Any]:
    dataset: ManufacturingDataset = normalize_manufacturing_dataframe(
        dataframe,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        cycle_time_unit=cycle_time_unit,
        duration_unit=duration_unit,
        production_unit=production_unit,
    )
    records = dataset.records
    generic_analytics = analyze_manufacturing_dataset(dataset)
    evidence = list(generic_analytics["evidence_references"])
    production = sum(record.production for record in records)
    planned_records = [
        record for record in records if record.planned_production is not None
    ]
    planned = (
        sum(record.planned_production or 0 for record in planned_records)
        if planned_records
        else None
    )
    actual_against_plan = (
        sum(record.production for record in planned_records)
        if planned_records
        else None
    )
    available_explicit, available_basis = _time_minutes(
        records,
        "available_time_minutes",
    )
    elapsed_minutes, elapsed_basis = _time_minutes(
        records,
        "available_time_minutes",
        allow_elapsed_fallback=True,
    )
    production_rate_minutes = (
        available_explicit
        if available_explicit is not None
        else elapsed_minutes
    )
    production_rate_basis = (
        available_basis
        if available_explicit is not None
        else elapsed_basis
    )
    production_rate_records = [
        record
        for record in records
        if record.available_time_minutes is not None
    ]
    if available_explicit is None:
        production_rate_records = [
            record
            for record in records
            if _elapsed_minutes(record) is not None
        ]
    operating_explicit, operating_basis = _time_minutes(
        records,
        "operating_time_minutes",
    )
    operating_elapsed, elapsed_operating_basis = _time_minutes(
        records,
        "operating_time_minutes",
        allow_elapsed_fallback=True,
    )
    operating = (
        operating_explicit
        if operating_explicit is not None
        else operating_elapsed
    )
    if operating_explicit is None:
        operating_basis = elapsed_operating_basis
    operating_records = [
        record
        for record in records
        if record.operating_time_minutes is not None
    ]
    if not operating_records and operating_elapsed is not None:
        operating_records = [
            record
            for record in records
            if _elapsed_minutes(record) is not None
        ]
    operating_production = sum(record.production for record in operating_records)
    paired_plan_time_records = [
        record
        for record in records
        if record.planned_production is not None
        and record.available_time_minutes is not None
    ]
    paired_utilization_records = [
        record
        for record in records
        if record.operating_time_minutes is not None
        and record.available_time_minutes is not None
    ]
    paired_plan_available_minutes, _ = _time_minutes(
        paired_plan_time_records,
        "available_time_minutes",
    )
    paired_utilization_operating_minutes, _ = _time_minutes(
        paired_utilization_records,
        "operating_time_minutes",
    )
    paired_utilization_available_minutes, _ = _time_minutes(
        paired_utilization_records,
        "available_time_minutes",
    )
    standard_cycle_records = [
        record
        for record in records
        if record.standard_cycle_time_seconds is not None
        and record.available_time_minutes is not None
    ]
    standard_available_minutes, _ = _time_minutes(
        standard_cycle_records,
        "available_time_minutes",
    )
    waiting_minutes, _ = _time_minutes(records, "waiting_time_minutes")
    cycle_records = [
        record
        for record in records
        if record.cycle_time_seconds is not None
    ]
    explicit_cycle_time = (
        sum(
            (record.cycle_time_seconds or 0) * record.production
            for record in cycle_records
        )
        / sum(record.production for record in cycle_records)
        if cycle_records
        and sum(record.production for record in cycle_records) > 0
        else None
    )
    cycle_time = explicit_cycle_time
    cycle_time_basis = "production_weighted_explicit_cycle_time"
    if (
        cycle_time is None
        and operating
        and operating > 0
        and operating_production > 0
    ):
        cycle_time = operating * 60 / operating_production
        cycle_time_basis = "operating_time_divided_by_actual_production"
    if cycle_time is None:
        cycle_time_basis = None

    kpi_results = {
        "planned_production": _metric(
            planned,
            dataset.units["production"],
            "sum_of_explicit_plan_values",
            len(planned_records),
        ),
        "actual_production": _metric(
            production,
            dataset.units["production"],
            "sum_of_valid_production_records",
            len(records),
        ),
        "production_achievement_rate": _metric(
            actual_against_plan / planned * 100
            if planned is not None and planned > 0
            and actual_against_plan is not None
            else None,
            "percent",
            "actual_on_planned_rows_divided_by_explicit_plan",
            len(planned_records),
        ),
        "plan_variance": _metric(
            actual_against_plan - planned
            if planned is not None and actual_against_plan is not None
            else None,
            dataset.units["production"],
            "actual_minus_explicit_plan",
            len(planned_records),
        ),
        "production_rate": _metric(
            sum(record.production for record in production_rate_records)
            / (production_rate_minutes / 60)
            if production_rate_minutes and production_rate_minutes > 0
            else None,
            f"{dataset.units['production']}/hour",
            f"actual_divided_by_{production_rate_basis or 'missing_available_time'}",
            len(production_rate_records),
        ),
        "throughput": _metric(
            operating_production / (operating / 60)
            if operating and operating > 0
            else None,
            f"{dataset.units['production']}/operating_hour",
            f"actual_divided_by_{operating_basis or 'missing_operating_time'}",
            len(operating_records),
        ),
        "cycle_time": _metric(
            cycle_time,
            "seconds/unit",
            cycle_time_basis or "insufficient_cycle_or_operating_time_data",
            len(cycle_records) if cycle_records else len(records),
        ),
        "takt_time": _metric(
            paired_plan_available_minutes * 60
                / sum(
                    record.planned_production or 0
                    for record in paired_plan_time_records
                )
            if paired_plan_time_records
            and paired_plan_available_minutes is not None
            and sum(
                record.planned_production or 0
                for record in paired_plan_time_records
            )
            > 0
            else None,
            "seconds/unit",
            "available_time_divided_by_explicit_plan",
            len(paired_plan_time_records),
        ),
        "line_efficiency": _metric(
            sum(
                record.production * (record.standard_cycle_time_seconds or 0)
                for record in standard_cycle_records
            )
            / (standard_available_minutes * 60)
            * 100
            if standard_cycle_records
            and standard_available_minutes is not None
            and standard_available_minutes > 0
            else None,
            "percent",
            "ideal_standard_cycle_time_load_divided_by_available_time",
            len(records),
        ),
        "line_utilization": _metric(
            paired_utilization_operating_minutes
            / paired_utilization_available_minutes
            * 100
            if operating_explicit is not None
            and paired_utilization_records
            and paired_utilization_operating_minutes is not None
            and paired_utilization_available_minutes is not None
            and paired_utilization_available_minutes > 0
            else None,
            "percent",
            "operating_time_divided_by_available_time",
            len(paired_utilization_records),
        ),
        "waiting_time": _metric(
            waiting_minutes
            if any(record.waiting_time_minutes is not None for record in records)
            else None,
            "minutes",
            "sum_of_explicit_waiting_time",
            sum(record.waiting_time_minutes is not None for record in records),
        ),
    }
    for metric_name, metric in kpi_results.items():
        if metric["value"] is not None:
            metric["evidence_id"] = _add_evidence(
                evidence,
                metric=metric_name,
                value=metric["value"],
                unit=metric["unit"],
                method=metric["basis"],
                record_count=metric["record_count"],
            )
        else:
            metric["evidence_id"] = None

    dimensions: dict[str, list[dict[str, Any]]] = {
        dimension: _breakdown(
            records,
            dimension,  # type: ignore[arg-type]
            production_unit=dataset.units["production"],
        )
        for dimension in ("line", "product", "shift", "process", "machine")
    }
    for dimension, rows in dimensions.items():
        for row in rows:
            row["evidence_id"] = _add_evidence(
                evidence,
                metric=f"{dimension}_production_performance",
                value={
                    "actual_production": row["actual_production"],
                    "actual_on_planned_rows": row["actual_on_planned_rows"],
                    "planned_production": row["planned_production"],
                    "production_achievement_rate": row[
                        "production_achievement_rate"
                    ],
                    "throughput": row["throughput"],
                    "cycle_time_seconds": row["cycle_time_seconds"],
                },
                unit=dataset.units["production"],
                method="grouped_production_performance",
                record_count=row["record_count"],
                dimensions={dimension: row["entity"]},
            )

    daily = _periods(
        records,
        "day",
        production_unit=dataset.units["production"],
        source_timezone=dataset.source_timezone,
    )
    weekly = _periods(
        records,
        "week",
        production_unit=dataset.units["production"],
        source_timezone=dataset.source_timezone,
    )
    monthly = _periods(
        records,
        "month",
        production_unit=dataset.units["production"],
        source_timezone=dataset.source_timezone,
    )
    for row in daily:
        row["evidence_id"] = _add_evidence(
            evidence,
            metric="daily_production_performance",
            value={
                "actual_production": row["actual_production"],
                "planned_production": row["planned_production"],
                "production_achievement_rate": row[
                    "production_achievement_rate"
                ],
            },
            unit=dataset.units["production"],
            method="utc_day_aggregation",
            record_count=row["record_count"],
            period=row["period"],
        )

    machine_performance = [
        {
            "machine": row["entity"],
            "production": row["actual_production"],
            "defects": row["defects"],
            "defect_rate": row["defect_rate"],
            "downtime": row["downtime_minutes"],
        }
        for row in dimensions["machine"]
    ]
    daily_production = [
        {
            "date": row["period"],
            "production": row["actual_production"],
            "actual_on_planned_rows": row["actual_on_planned_rows"],
            "planned_production": row["planned_production"],
            "defect_rate": row["defect_rate"],
            "downtime": row["downtime_minutes"],
        }
        for row in daily
    ]

    anomalies = _detect_anomalies(
        daily,
        records,
        evidence,
        dataset.units["production"],
    )

    process_rows = dimensions["process"]
    process_throughputs = [
        row["throughput"]
        for row in process_rows
        if row["throughput"] is not None
    ]
    bottlenecks: list[dict[str, Any]] = []
    if len(process_rows) >= 2 and process_throughputs:
        lowest = min(
            (
                row
                for row in process_rows
                if row["throughput"] is not None
            ),
            key=lambda row: row["throughput"],
        )
        peer_median = statistics.median(process_throughputs)
        if lowest["throughput"] < peer_median or (
            lowest["waiting_time_minutes"] or 0
        ) > 0:
            evidence_id = _add_evidence(
                evidence,
                metric="potential_process_bottleneck",
                value={
                    "throughput": lowest["throughput"],
                    "peer_median_throughput": _rounded(peer_median),
                    "waiting_time_minutes": lowest["waiting_time_minutes"],
                },
                unit=f"{dataset.units['production']}/operating_hour",
                method="lowest_process_throughput_and_explicit_wait_time",
                record_count=lowest["record_count"],
                dimensions={"process": lowest["entity"]},
            )
            bottlenecks.append(
                {
                    "process": lowest["entity"],
                    "throughput": lowest["throughput"],
                    "peer_median_throughput": _rounded(peer_median),
                    "waiting_time_minutes": lowest["waiting_time_minutes"],
                    "status": "screening_candidate",
                    "line_impact": (
                        "not_estimated_without_process_sequence_and_capacity"
                    ),
                    "evidence_ids": [evidence_id],
                }
            )

    limitations = list(dataset.data_quality.warnings)
    if planned is None:
        limitations.append(
            "Không có cột kế hoạch rõ ràng; achievement, plan variance và takt time không được suy diễn."
        )
    if available_explicit is None:
        limitations.append(
            "Thiếu Available Time; takt time, utilization và line efficiency không được suy diễn."
        )
    if production_rate_minutes is None:
        limitations.append(
            "Thiếu Available Time và cặp Production Start/End; production rate chưa đủ cơ sở."
        )
    if operating is None:
        limitations.append(
            "Thiếu Operating Time hoặc Production Start/End; throughput và cycle time suy ra không đủ cơ sở."
        )
    if operating_explicit is None and operating_elapsed is not None:
        limitations.append(
            "Throughput/cycle time dùng Production Start/End là elapsed time, không đại diện thời gian chạy máy đã xác nhận."
        )
    if not any(
        record.standard_cycle_time_seconds is not None
        and record.available_time_minutes is not None
        for record in records
    ):
        limitations.append(
            "Thiếu Standard Cycle Time; line efficiency không được ước lượng."
        )
    if not dimensions["shift"]:
        limitations.append("Không có trường Shift để so sánh hiệu suất theo ca.")
    if len(process_rows) < 2:
        limitations.append(
            "Cần ít nhất hai công đoạn và Operating Time để sàng lọc bottleneck; không suy luận chuỗi công đoạn."
        )
    if len(daily) < _MIN_ANOMALY_BASELINE + 1:
        limitations.append(
            "Cần ít nhất tám ngày baseline và một ngày mới để phát hiện suy giảm theo IQR."
        )

    plan_vs_actual = {
        "planned_production": kpi_results["planned_production"],
        "actual_production_on_planned_rows": _metric(
            actual_against_plan,
            dataset.units["production"],
            "actual_on_rows_with_explicit_plan",
            len(planned_records),
        ),
        "variance": kpi_results["plan_variance"],
        "achievement_rate": kpi_results["production_achievement_rate"],
        "coverage_records": len(planned_records),
        "total_records": len(records),
    }
    if (
        plan_vs_actual["actual_production_on_planned_rows"]["value"] is not None
    ):
        plan_vs_actual["actual_production_on_planned_rows"]["evidence_id"] = (
            _add_evidence(
                evidence,
                metric="actual_production_on_planned_rows",
                value=actual_against_plan,
                unit=dataset.units["production"],
                method="sum_actual_where_plan_is_present",
                record_count=len(planned_records),
            )
        )

    weak_line = dimensions["line"][0] if dimensions["line"] else None
    if weak_line and weak_line["production_achievement_rate"] is None:
        weak_line = min(
            dimensions["line"],
            key=lambda row: (
                row["throughput"]
                if row["throughput"] is not None
                else row["actual_production"]
            ),
        )
    best_shift = None
    if dimensions["shift"]:
        shifts_with_plan = [
            row
            for row in dimensions["shift"]
            if row["production_achievement_rate"] is not None
        ]
        shifts_with_throughput = [
            row
            for row in dimensions["shift"]
            if row["throughput"] is not None
        ]
        shift_ranking = (
            shifts_with_plan
            if shifts_with_plan
            else shifts_with_throughput
            if shifts_with_throughput
            else dimensions["shift"]
        )
        ranking_key = (
            "production_achievement_rate"
            if shifts_with_plan
            else "throughput"
            if shifts_with_throughput
            else "actual_production"
        )
        best_shift = max(shift_ranking, key=lambda row: row[ranking_key])
    answers = {
        "weakest_line": (
            {
                "answer": (
                    f"{weak_line['entity']} có thấp nhất theo "
                    f"{'production achievement' if weak_line['production_achievement_rate'] is not None else 'throughput' if weak_line['throughput'] is not None else 'actual production'}."
                ),
                "evidence_ids": [weak_line["evidence_id"]],
            }
            if weak_line
            else None
        ),
        "plan_shortfall": (
            {
                "answer": (
                    f"Sản lượng thấp hơn kế hoạch {abs(kpi_results['plan_variance']['value']):g} "
                    f"{dataset.units['production']}."
                    if kpi_results["plan_variance"]["value"] is not None
                    and kpi_results["plan_variance"]["value"] < 0
                    else "Sản lượng không thấp hơn kế hoạch trong các dòng có kế hoạch."
                ),
                "evidence_ids": [
                    kpi_results["plan_variance"]["evidence_id"]
                ],
            }
            if kpi_results["plan_variance"]["evidence_id"]
            else None
        ),
        "best_shift": (
            {
                "answer": (
                    f"{best_shift['entity']} dẫn đầu theo "
                    f"{'production achievement' if best_shift['production_achievement_rate'] is not None else 'throughput' if best_shift['throughput'] is not None else 'actual production'}."
                ),
                "evidence_ids": [best_shift["evidence_id"]],
            }
            if best_shift
            else None
        ),
        "bottleneck_process": (
            {
                "answer": (
                    f"{bottlenecks[0]['process']} là ứng viên cần kiểm tra; "
                    "đây là sàng lọc throughput, chưa xác nhận quan hệ nhân quả."
                ),
                "evidence_ids": bottlenecks[0]["evidence_ids"],
            }
            if bottlenecks
            else None
        ),
        "production_decline": (
            {
                "answer": (
                    "Có tín hiệu suy giảm cần xác minh ở kỳ "
                    f"{next(item['period'] for item in anomalies if item['type'] == 'production_decline')}."
                ),
                "evidence_ids": next(
                    item["evidence_ids"]
                    for item in anomalies
                    if item["type"] == "production_decline"
                ),
            }
            if any(item["type"] == "production_decline" for item in anomalies)
            else None
        ),
    }
    recommendations: list[dict[str, Any]] = [
        {
            "action": "Đối chiếu ca hoặc dây chuyền có achievement thấp với kế hoạch và lịch vận hành.",
            "priority": "high",
            "evidence_ids": [weak_line["evidence_id"]],
        }
        for _ in [0]
        if weak_line and weak_line["production_achievement_rate"] is not None
    ]
    recommendations.extend(
        {
            "action": (
                f"Kiểm tra dữ liệu chờ và thời gian vận hành tại công đoạn "
                f"{item['process']} trước khi kết luận bottleneck."
            ),
            "priority": "medium",
            "evidence_ids": item["evidence_ids"],
        }
        for item in bottlenecks
    )
    if not recommendations:
        recommendations.append(
            {
                "action": "Bổ sung kế hoạch, thời gian khả dụng/vận hành và dữ liệu ca để tính hiệu suất có mẫu số kiểm chứng.",
                "priority": "medium",
                "evidence_ids": [
                    kpi_results["actual_production"]["evidence_id"]
                ],
            }
        )

    analytics: dict[str, Any] = {
        "schema_version": "2.1",
        "data_quality": dataset.data_quality.model_dump(mode="json"),
        "data_structure": {
            "rows_used": len(records),
            "source_columns": list(map(str, dataframe.columns)),
            "mapped_columns": dataset.column_mapping,
            "product_count": len({record.product for record in records if record.product}),
            "process_count": len({record.process for record in records if record.process}),
            "line_count": len({record.line for record in records if record.line}),
            "shift_count": len({record.shift for record in records if record.shift}),
            "timestamp_count": sum(record.timestamp is not None for record in records),
            "production_interval_count": sum(
                record.production_start is not None
                and record.production_end is not None
                for record in records
            ),
        },
        "kpi_results": kpi_results,
        "plan_vs_actual": plan_vs_actual,
        "production_trends": {
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
        },
        "line_performance": dimensions["line"],
        "product_performance": dimensions["product"],
        "shift_performance": dimensions["shift"],
        "process_performance": dimensions["process"],
        "machine_performance": machine_performance,
        "machine_performance_by_dimension": dimensions["machine"],
        "daily_production": daily_production,
        "legacy_machine_performance": machine_performance,
        "data_scope": {
            "has_machine_data": bool(dimensions["machine"]),
            "has_defect_data": any(
                record.defects is not None for record in records
            ),
            "has_downtime_data": any(
                record.downtime_minutes is not None for record in records
            ),
            "has_date_data": any(record.timestamp is not None for record in records),
            "has_plan_data": bool(planned_records),
            "has_shift_data": bool(dimensions["shift"]),
            "has_line_data": bool(dimensions["line"]),
            "has_cycle_time_data": any(
                record.cycle_time_seconds is not None for record in records
            ),
        },
        "bottleneck_analysis": {
            "candidates": bottlenecks,
            "method": (
                "lowest process throughput against peer median with explicit "
                "wait-time signal; screening only, no causal or capacity proof"
            ),
        },
        "production_anomalies": anomalies,
        "management_answers": answers,
        "recommendations": recommendations,
        "confidence_assessment": generic_analytics["confidence_assessment"],
        "evidence_references": evidence,
        "analysis_limitations": list(dict.fromkeys(limitations)),
        "executed_modules": [
            "production_data_analysis",
            "production_kpi_engine",
            "production_performance_analytics",
            "bottleneck_intelligence",
            "production_anomaly_detection",
            "evidence_generation",
        ],
        "ai_status": "not_requested",
        "ai_error": None,
        "ai_interpretation": None,
    }

    if include_ai:
        interpretation, ai_status, ai_error = _interpret_with_gemini(analytics)
        analytics["ai_status"] = ai_status
        analytics["ai_error"] = ai_error
        if interpretation is not None:
            analytics["ai_interpretation"] = interpretation.model_dump(mode="json")
    analytics["executive_summary"] = (
        analytics["ai_interpretation"]["executive_summary"]["text"]
        if analytics["ai_interpretation"]
        else (
            f"Đã phân tích {len(records)} dòng hợp lệ, tổng sản lượng "
            f"{production:g} {dataset.units['production']}."
        )
    )
    analytics["data_contract"] = dataset.model_dump(
        mode="json",
        exclude={"records"},
    )
    return analytics
