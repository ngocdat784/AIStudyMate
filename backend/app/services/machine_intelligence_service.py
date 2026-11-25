from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from typing import Any

import pandas as pd

from app.services.manufacturing_data_foundation import (
    ManufacturingRecord,
    normalize_manufacturing_dataframe,
)


_FAILURE_WORDS = (
    "failure", "failed", "fault", "breakdown", "error", "repair", "hỏng", "lỗi",
)
_STOP_WORDS = ("stop", "stopped", "down", "idle", "off", "halted", "dừng", "ngừng")


def _round(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _unique_total(records: list[ManufacturingRecord], field: str) -> float | None:
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
            record.timestamp.date() if record.timestamp else None,
            record.production_start,
            record.production_end,
            float(value),
        )
        if not any(key[:-1]) and record.source_row:
            key += (record.source_row,)
        if key in seen:
            continue
        seen.add(key)
        values.append(float(value))
    return sum(values) if values else None


def _status_kind(status: str | None) -> str:
    normalized = (status or "").strip().casefold()
    if not normalized:
        return "unknown"
    if any(word in normalized for word in (
        "sensor",
        "instrument",
        "software",
        "calibration",
        "electrical",
    )) and not any(word in normalized for word in (
        "breakdown",
        "failed",
        "failure",
        "hỏng",
   )):
        return "other"
    if any(word in normalized for word in _FAILURE_WORDS):
        return "failure"
    if any(word in normalized for word in _STOP_WORDS):
        return "stopped"
    if any(word in normalized for word in ("run", "running", "production", "chạy", "hoạt động")):
        return "running"
    return "other"


def is_machine_failure(record: ManufacturingRecord) -> bool:
    if record.downtime_minutes is None or record.downtime_minutes <= 0:
        return False

    status = (record.machine_status or "").strip().casefold()
    reason = (record.downtime_type or "").strip().casefold()
    excluded_fault_context = any(
        word in f"{status} {reason}"
        for word in (
            "sensor",
            "instrument",
            "software",
            "calibration",
        )
    )
    if excluded_fault_context:
        return False

    confirmed_failure_markers = (
        "breakdown",
        "failed",
        "failure",
        "mechanical",
        "hỏng",
        "hung",
        "motor fault",
        "repair",
    )
    if any(marker in status or marker in reason for marker in confirmed_failure_markers):
        return True

    return False


def _normalize_downtime_reason(reason: str | None) -> str:
    normalized = (reason or "").strip().casefold()
    if not normalized or normalized in {
        "none",
        "n/a",
        "na",
        "null",
        "unknown",
        "unclassified",
        "not specified",
    }:
        return "unknown"
    return (reason or "").strip()


MAINTENANCE_SUCCESS_MARKERS = (
    "completed",
    "complete",
    "passed",
    "pass",
    "success",
    "successful",
    "succeeded",
    "fixed",
    "resolved",
    "restored",
    "repaired",
    "verified",
    "validated",
    "approved",
    "good",
    "ok",
    "done",
    "hoàn thành",
    "hoan thanh",
    "thành công",
    "thanh cong",
    "đạt",
    "dat",
    "sửa chữa xong",
    "sua chua xong",
    "khôi phục",
    "khoi phuc",
)

MAINTENANCE_FAILURE_MARKERS = (
    "failed",
    "failure",
    "unsuccessful",
    "not passed",
    "not_passed",
    "not successful",
    "not successful",
    "rejected",
    "aborted",
    "incomplete",
    "not complete",
    "not completed",
    "ng",
    "fault",
    "broken",
    "unrepaired",
    "thất bại",
    "that bai",
    "không đạt",
    "khong dat",
    "không hoàn thành",
    "khong hoan thanh",
    "chưa hoàn thành",
    "chua hoan thanh",
    "không thành công",
    "khong thanh cong",
    "từ chối",
    "tu choi",
)

MAINTENANCE_MONITORING_MARKERS = (
    "monitoring required",
    "monitor",
    "observation",
    "observe",
    "follow up",
    "follow-up",
    "recheck",
    "pending",
    "in progress",
    "waiting",
    "conditional",
    "caution",
    "cần giám sát",
    "can giam sat",
    "cần theo dõi",
    "can theo doi",
    "theo dõi",
    "theo doi",
    "đang giám sát",
    "dang giam sat",
    "đang theo dõi",
    "dang theo doi",
    "đang thực hiện",
    "dang thuc hien",
    "cần kiểm tra lại",
    "can kiem tra lai",
    "chờ",
    "cho",
)


def _normalize_maintenance_status(outcome: str | None) -> str:
    normalized = " ".join((outcome or "").strip().casefold().split())
    compact = normalized.replace("_", " ").replace("-", " ")
    compact = " ".join(compact.split())
    if not normalized or normalized in {
        "none",
        "n/a",
        "na",
        "null",
        "unknown",
        "unclassified",
        "not specified",
    }:
        return "unknown"

    searchable = f"{normalized} {compact}"
    def contains_marker(marker: str) -> bool:
        needle = " ".join(marker.strip().casefold().replace("_", " ").replace("-", " ").split())
        if not needle:
            return False
        if len(needle) <= 3:
            return bool(re.search(
                rf"(?<!\w){re.escape(needle)}(?!\w)",
                searchable,
            ))
        return needle in searchable

    if any(contains_marker(marker) for marker in MAINTENANCE_FAILURE_MARKERS):
        return "failed"
    if any(contains_marker(marker) for marker in MAINTENANCE_MONITORING_MARKERS):
        return "monitoring"
    if any(contains_marker(marker) for marker in MAINTENANCE_SUCCESS_MARKERS):
        return "successful"
    return "unknown"


def _maintenance_status_counts(records: list[ManufacturingRecord]) -> dict[str, int]:
    counts = {
        "successful": 0,
        "failed": 0,
        "monitoring": 0,
        "unknown": 0,
    }
    for record in records:
        if record.maintenance_outcome:
            status = _normalize_maintenance_status(record.maintenance_outcome)
            counts[status] += 1
    return counts


def _performance_metrics(
    records: list[ManufacturingRecord],
) -> dict[str, float | int | None]:
    produced = sum(record.production for record in records)
    available = _unique_total(records, "available_time_minutes")
    operating = _unique_total(records, "operating_time_minutes")
    planned_downtime = sum(
        record.downtime_minutes or 0
        for record in records
        if record.downtime_planned is True
    )
    total_downtime = sum(
        record.downtime_minutes or 0
        for record in records
        if record.downtime_minutes is not None
    )

    planned_production_time = (
        max(0.0, available - planned_downtime)
        if available is not None
        else None
    )
    if operating is None and available is not None:
        operating = max(0.0, available - total_downtime)
    if planned_production_time is None and operating is not None and total_downtime:
        planned_production_time = operating + sum(
            record.downtime_minutes or 0
            for record in records
            if record.downtime_planned is not True
        )
    availability = (
        operating / planned_production_time
        if operating is not None and planned_production_time and planned_production_time > 0
        else None
    )

    ideal_seconds = sum(
        record.standard_cycle_time_seconds * record.production
        for record in records
        if record.standard_cycle_time_seconds is not None
        and record.production > 0
    )
    operating_seconds = operating * 60 if operating is not None else None
    performance = (
        ideal_seconds / operating_seconds
        if ideal_seconds > 0 and operating_seconds and operating_seconds > 0
        else None
    )

    first_pass_inspected = sum(
        record.first_pass_inspected or 0
        for record in records
        if record.first_pass_inspected is not None
        and record.first_pass_passed is not None
    )
    first_pass_passed = sum(
        record.first_pass_passed or 0
        for record in records
        if record.first_pass_inspected is not None
        and record.first_pass_passed is not None
    )
    inspected = sum(
        record.inspected_quantity or 0
        for record in records
        if record.inspected_quantity is not None
        and record.defective_units is not None
    )
    inspected_good = sum(
        (record.inspected_quantity or 0) - (record.defective_units or 0)
        for record in records
        if record.inspected_quantity is not None
        and record.defective_units is not None
    )
    quality: float | None = None
    if first_pass_inspected > 0:
        quality = first_pass_passed / first_pass_inspected
    elif inspected > 0:
        quality = inspected_good / inspected
    else:
        paired = [
            record
            for record in records
            if record.defects is not None and record.production > 0
        ]
        denominator = sum(record.production for record in paired)
        defects = sum(record.defects or 0 for record in paired)
        if denominator > 0 and defects <= denominator:
            quality = (denominator - defects) / denominator

    valid_oee = all(
        value is not None and 0 <= value <= 1
        for value in (availability, performance, quality)
    )
    oee = (
        availability * performance * quality
        if valid_oee
        else None
    )
    failure_records = [record for record in records if is_machine_failure(record)]
    failures = len(failure_records)
    unplanned_downtime = sum(
        record.downtime_minutes or 0
        for record in records
        if record.downtime_planned is not True
    )
    failure_downtime = sum(
        record.downtime_minutes or 0
        for record in failure_records
        if record.downtime_minutes is not None
        and record.downtime_minutes > 0
    )
    performance_records = [
        record
        for record in records
        if record.production > 0
        and record.standard_cycle_time_seconds is not None
        and record.standard_cycle_time_seconds > 0
        and record.operating_time_minutes is not None
        and record.operating_time_minutes > 0
    ]
    performance_validation = (
        "valid"
        if performance is not None and 0 <= performance <= 1
        else "above_100_check_units"
        if performance is not None and performance > 1
        else "insufficient_data"
    )
    downtime_event_records = [
        record
        for record in records
        if record.downtime_minutes is not None
        and record.downtime_minutes > 0
    ]
    maintenance_event_records = [
        record
        for record in records
        if record.maintenance_action or record.maintenance_outcome
    ]
    return {
        "production": _round(produced),
        "available_time_minutes": _round(available),
        "operating_time_minutes": _round(operating),
        "planned_production_time_minutes": _round(planned_production_time),
        "downtime_minutes": _round(total_downtime),
        "planned_downtime_minutes": _round(planned_downtime),
        "unplanned_downtime_minutes": _round(unplanned_downtime),
        "availability_percent": _round(availability * 100 if availability is not None else None),
        "performance_percent": _round(performance * 100 if performance is not None else None),
        "quality_percent": _round(quality * 100 if quality is not None else None),
        "oee_percent": _round(oee * 100 if oee is not None else None),
        "performance_validation": performance_validation,
        "performance_record_count": len(performance_records),
        "machine_utilization_percent": _round(
            operating / available * 100
            if operating is not None and available and available > 0
            else None
        ),
        "failure_count": failures,
        "mtbf_hours": _round(operating / 60 / failures if operating is not None and failures else None),
        "mttr_minutes": _round(failure_downtime / failures if failures else None),
        "downtime_event_count": len(downtime_event_records),
        "maintenance_event_count": len(maintenance_event_records),
        "record_count": len(records),
    }


def _grouped_metrics(
    records: list[ManufacturingRecord],
    dimension: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        entity = getattr(record, dimension)
        if entity:
            grouped[entity].append(record)
    return [
        {"entity": name, **_performance_metrics(group_records)}
        for name, group_records in sorted(grouped.items())
    ]


def _machine_events(records: list[ManufacturingRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        status_kind = _status_kind(record.machine_status)
        if (
            record.downtime_minutes is None
            and status_kind == "unknown"
            and not record.downtime_type
        ):
            continue
        planned = record.downtime_planned
        positive_downtime = (
            record.downtime_minutes is not None
            and record.downtime_minutes > 0
        )
        if planned is None and positive_downtime and (
            status_kind == "failure" or _status_kind(record.downtime_type) == "failure"
        ):
            planned = False
        event_type = (
            "planned_downtime"
            if planned is True
            else "unplanned_downtime"
            if (
                positive_downtime
                and (
                    planned is False
                    or status_kind == "failure"
                    or _status_kind(record.downtime_type) == "failure"
                )
            )
            else "unclassified_downtime"
            if record.downtime_minutes is not None and record.downtime_minutes > 0
            else "machine_state"
        )
        events.append(
            {
                "machine": record.machine or "Unknown",
                "line": record.line,
                "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                "event_type": event_type,
                "machine_status": record.machine_status,
                "status_class": status_kind,
                "duration_minutes": _round(record.downtime_minutes),
                "reason": _normalize_downtime_reason(record.downtime_type),
                "downtime_classification": (
                    "planned"
                    if planned is True
                    else "unplanned"
                    if positive_downtime
                    and (
                        planned is False
                        or status_kind == "failure"
                        or _status_kind(record.downtime_type) == "failure"
                    )
                    else "unclassified"
                ),
                "source_row": record.source_row,
            }
        )
    return sorted(
        events,
        key=lambda item: (item["timestamp"] or "", item["source_row"]),
    )


def _downtime_report(
    records: list[ManufacturingRecord],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    duration_by_reason: dict[str, float] = defaultdict(float)
    count_by_reason: dict[str, int] = defaultdict(int)
    planned = unplanned = 0.0
    event_count = 0
    for record in records:
        duration = record.downtime_minutes
        if duration is None or duration <= 0:
            continue
        event_count += 1
        if record.downtime_planned is True:
            planned += duration
            classification = "planned"
        elif (
            record.downtime_planned is False
            or _status_kind(record.machine_status) == "failure"
            or _status_kind(record.downtime_type) == "failure"
        ):
            unplanned += duration
            classification = "unplanned"
        else:
            classification = "unclassified"
        reason = _normalize_downtime_reason(record.downtime_type)
        duration_by_reason[reason] += duration
        count_by_reason[reason] += 1
    total = sum(duration_by_reason.values())
    pareto: list[dict[str, Any]] = []
    cumulative = 0.0
    for reason, duration in sorted(
        duration_by_reason.items(),
        key=lambda item: (-item[1], item[0].casefold()),
    ):
        cumulative += duration
        pareto.append(
            {
                "reason": reason,
                "duration_minutes": _round(duration),
                "event_count": count_by_reason[reason],
                "share_percent": _round(duration / total * 100 if total else None),
                "cumulative_share_percent": _round(cumulative / total * 100 if total else None),
            }
        )
    return {
        "planned_downtime_minutes": _round(planned),
        "unplanned_downtime_minutes": _round(unplanned),
        "unclassified_downtime_minutes": _round(max(0.0, total - planned - unplanned)),
        "total_downtime_minutes": _round(total),
        "event_count": event_count,
        "frequency_by_reason": [
            {"reason": reason, "event_count": count_by_reason[reason]}
            for reason in sorted(count_by_reason, key=lambda key: (-count_by_reason[key], key.casefold()))
        ],
        "pareto": pareto,
        "normalized_events": events,
    }


def _detect_anomalies(
    records: list[ManufacturingRecord],
    machine_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    by_machine: dict[str, list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if record.machine:
            by_machine[record.machine].append(record)
    for machine, machine_records in by_machine.items():
        downtime = [
            float(record.downtime_minutes)
            for record in machine_records
            if record.downtime_minutes is not None and record.downtime_minutes > 0
        ]
        if len(downtime) >= 8:
            quartiles = statistics.quantiles(downtime, n=4, method="inclusive")
            q1, q3 = quartiles[0], quartiles[2]
            latest = max(
                (record for record in machine_records if record.downtime_minutes is not None),
                key=lambda record: (
                    record.timestamp.isoformat() if record.timestamp else "",
                    record.source_row,
                ),
                default=None,
            )
            if latest and latest.downtime_minutes and latest.downtime_minutes > q3 + 1.5 * (q3 - q1):
                findings.append(
                    {
                        "machine": machine,
                        "type": "unusual_downtime_duration",
                        "observed_minutes": _round(latest.downtime_minutes),
                        "upper_bound_minutes": _round(q3 + 1.5 * (q3 - q1)),
                        "baseline_event_count": len(downtime),
                        "source_row": latest.source_row,
                    }
                )
        cycles = [
            float(record.cycle_time_seconds)
            for record in machine_records
            if record.cycle_time_seconds is not None
        ]
        if len(cycles) >= 8:
            quartiles = statistics.quantiles(cycles, n=4, method="inclusive")
            latest_cycle = max(
                (
                    record
                    for record in machine_records
                    if record.cycle_time_seconds is not None
                ),
                key=lambda record: (
                    record.timestamp.isoformat() if record.timestamp else "",
                    record.source_row,
                ),
                default=None,
            )
            upper = quartiles[2] + 1.5 * (quartiles[2] - quartiles[0])
            if latest_cycle and latest_cycle.cycle_time_seconds and latest_cycle.cycle_time_seconds > upper:
                findings.append(
                    {
                        "machine": machine,
                        "type": "unusual_cycle_time",
                        "observed_seconds": _round(latest_cycle.cycle_time_seconds),
                        "upper_bound_seconds": _round(upper),
                        "baseline_record_count": len(cycles),
                        "source_row": latest_cycle.source_row,
                    }
                )
    counts = [int(item["failure_count"]) for item in machine_metrics]
    if len(counts) >= 2:
        quartiles = statistics.quantiles(counts, n=4, method="inclusive")
        upper = quartiles[2] + 1.5 * (quartiles[2] - quartiles[0])
        findings.extend(
            {
                "machine": item["entity"],
                "type": "high_failure_frequency",
                "failure_count": item["failure_count"],
                "peer_upper_bound": _round(upper),
                "peer_count": len(machine_metrics),
                "confidence": (
                    "moderate"
                    if len(machine_metrics) >= 4
                    else "low"
                ),
            }
            for item in machine_metrics
            if item["failure_count"] >= 2 and item["failure_count"] > upper
        )

    for machine, machine_records in by_machine.items():
        timed = sorted(
            (
                record
                for record in machine_records
                if record.timestamp is not None
                and record.standard_cycle_time_seconds
                and record.operating_time_minutes
                and record.operating_time_minutes > 0
            ),
            key=lambda record: record.timestamp,
        )
        if len(timed) < 8:
            continue
        midpoint = len(timed) // 2
        if midpoint < 4:
            continue
        def period_performance(items: list[ManufacturingRecord]) -> float:
            run_seconds = sum(record.operating_time_minutes or 0 for record in items) * 60
            ideal_seconds = sum(
                (record.standard_cycle_time_seconds or 0) * record.production
                for record in items
            )
            return ideal_seconds / run_seconds if run_seconds else 0
        earlier = period_performance(timed[:midpoint])
        later = period_performance(timed[midpoint:])
        if earlier > 0 and (earlier - later) / earlier >= 0.1:
            findings.append(
                {
                    "machine": machine,
                    "type": "performance_decline",
                    "earlier_performance_percent": _round(earlier * 100),
                    "later_performance_percent": _round(later * 100),
                    "decline_percent": _round((earlier - later) / earlier * 100),
                    "method": "first_half_vs_second_half_of_timestamped_records",
                    "sample_count": len(timed),
                    "earlier_sample_count": midpoint,
                    "later_sample_count": len(timed) - midpoint,
                    "confidence": (
                        "high"
                        if len(timed) >= 12
                        else "moderate"
                        if len(timed) >= 8
                        else "low"
                    ),
                }
            )
    return findings


def analyze_machine_intelligence(
    dataframe: pd.DataFrame,
    *,
    column_mapping: dict[str, str] | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    duration_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
) -> dict[str, Any]:
    dataset = normalize_manufacturing_dataframe(
        dataframe,
        column_mapping=column_mapping,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        duration_unit=duration_unit,
        cycle_time_unit=cycle_time_unit,
        require_production=False,
    )
    records = dataset.records
    machine_metrics = _grouped_metrics(records, "machine")
    line_metrics = _grouped_metrics(records, "line")
    shift_metrics = _grouped_metrics(records, "shift")
    events = _machine_events(records)
    downtime = _downtime_report(records, events)
    anomalies = _detect_anomalies(records, machine_metrics)
    maintenance: list[dict[str, Any]] = []
    for item in machine_metrics:
        failures = int(item["failure_count"])
        unplanned_minutes = float(item["unplanned_downtime_minutes"] or 0)
        downtime_events = int(item["downtime_event_count"])
        maintenance_events = int(item["maintenance_event_count"])
        risk = (
            "high"
            if failures >= 2
            or (failures >= 1 and unplanned_minutes >= 60)
            else "medium"
            if failures >= 1 or unplanned_minutes >= 30
            else "low"
        )
        if risk != "low":
            machine_history = [
                record
                for record in records
                if record.machine == item["entity"]
                and (record.maintenance_action or record.maintenance_outcome)
            ]
            outcome_counts = _maintenance_status_counts(machine_history)
            passed_outcomes = outcome_counts["successful"]
            success_rate = (
                round(
                    passed_outcomes
                    / sum(
                        outcome_counts[key]
                        for key in ("successful", "failed", "monitoring")
                    )
                    * 100,
                    4,
                )
                if sum(
                    outcome_counts[key]
                    for key in ("successful", "failed", "monitoring")
                )
                else None
            )
            maintenance.append(
                {
                    "machine": item["entity"],
                    "risk": risk,
                    "failure_count": failures,
                    "unplanned_downtime_minutes": _round(unplanned_minutes),
                    "downtime_event_count": downtime_events,
                    "maintenance_event_count": maintenance_events,
                    "maintenance_passed_count": passed_outcomes,
                    "maintenance_failed_count": outcome_counts["failed"],
                    "maintenance_monitoring_count": outcome_counts["monitoring"],
                    "maintenance_unknown_count": outcome_counts["unknown"],
                    "maintenance_success_rate_percent": success_rate,
                    "recommended_priority": "urgent" if risk == "high" else "planned",
                    "recommendation": (
                        "Ưu tiên kiểm tra nguyên nhân hỏng lặp lại và xác nhận an toàn trước khi vận hành."
                        if risk == "high"
                        else "Đưa thiết bị vào đợt kiểm tra/bảo trì kế hoạch gần nhất."
                    ),
                    "maintenance_history": [
                        {
                            "action": record.maintenance_action,
                            "outcome": record.maintenance_outcome,
                            "outcome_status": _normalize_maintenance_status(
                                record.maintenance_outcome
                            ),
                            "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                            "source_row": record.source_row,
                        }
                        for record in records
                        if record.machine == item["entity"]
                        and (record.maintenance_action or record.maintenance_outcome)
                    ],
                }
            )
    maintenance.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}[item["risk"]],
            -item["failure_count"],
            -float(item["unplanned_downtime_minutes"] or 0),
        )
    )

    limitations: list[str] = []
    if not any(record.machine for record in records):
        limitations.append("Không có Machine ID/name; chỉ có thể tính chỉ số tổng thể.")
    if not any(record.available_time_minutes is not None for record in records):
        limitations.append("Thiếu Available Time nên Availability/OEE không đầy đủ.")
    if not any(record.standard_cycle_time_seconds is not None for record in records):
        limitations.append("Thiếu Standard Cycle Time nên chưa thể tính Performance/OEE.")
    if not any(record.defects is not None or record.inspected_quantity is not None for record in records):
        limitations.append("Thiếu dữ liệu lỗi/kiểm định nên chưa thể tính Quality/OEE.")
    if not any(record.downtime_minutes is not None for record in records):
        limitations.append("Thiếu Downtime; phân loại sự kiện dừng và độ tin cậy bị giới hạn.")
    if not any(record.timestamp is not None for record in records):
        limitations.append("Thiếu timestamp; không thể xác định thứ tự trạng thái và xu hướng.")
    if any(
        item["performance_percent"] is not None
        and item["performance_percent"] > 100
        for item in machine_metrics
    ):
        limitations.append(
            "Performance vượt 100% ở một hoặc nhiều máy; kiểm tra đơn vị và tính nhất quán của Production, Standard Cycle Time và Operating Time trước khi diễn giải."
        )
    if any(
        item["performance_record_count"] < 8
        for item in machine_metrics
    ):
        limitations.append(
            "Mẫu Performance chưa đạt 8 quan sát hợp lệ cho một số máy; không phát cảnh báo suy giảm Performance khi chưa đủ mẫu."
        )
    if any(item["oee_percent"] is None for item in machine_metrics):
        limitations.append("OEE chỉ được tính khi Availability, Performance và Quality đều có mẫu số hợp lệ và nằm trong khoảng 0–100%.")
    return {
        "schema_version": "1.1",
        "analysis_type": "machine_intelligence",
        "data_quality": dataset.data_quality.model_dump(mode="json"),
        "machine_count": len(machine_metrics),
        "overall": _performance_metrics(records),
        "by_machine": machine_metrics,
        "by_line": line_metrics,
        "by_shift": shift_metrics,
        "downtime": downtime,
        "anomalies": anomalies,
        "maintenance_priorities": maintenance,
        "maintenance_outcomes": [
            {
                "machine": record.machine,
                "action": record.maintenance_action,
                "outcome": record.maintenance_outcome,
                "outcome_status": _normalize_maintenance_status(
                    record.maintenance_outcome
                ),
                "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                "source_row": record.source_row,
            }
            for record in records
            if record.maintenance_action or record.maintenance_outcome
        ],
        "analysis_limitations": limitations,
    }
