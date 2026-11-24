from datetime import datetime, timezone

from unittest.mock import patch

import pandas as pd
import pytest

from app.services.manufacturing_analytics_engine import (
    analyze_manufacturing_dataset,
)
from app.services.manufacturing_data_foundation import (
    normalize_manufacturing_dataframe,
)
from app.services.intelligence_orchestrator import (
    EvidenceBoundAIInterpretation,
    EvidenceBoundStatement,
    _validate_ai_evidence,
    orchestrate_manufacturing_analysis,
)
from app.services.machine_intelligence_service import analyze_machine_intelligence
from app.services import hardware_service


def test_normalization_maps_aliases_and_tracks_invalid_duplicate_rows():
    dataframe = pd.DataFrame(
        [
            {
                "Date": "2026-03-08 01:30:00",
                "Production": "10",
                "Defects": "1",
                "Downtime": "3600",
                "Line": "Line A",
            },
            {
                "Date": "2026-03-08 01:30:00",
                "Production": "10",
                "Defects": "1",
                "Downtime": "3600",
                "Line": "Line A",
            },
            {
                "Date": "2026-03-09 01:30:00",
                "Production": "20",
                "Defects": "not-a-number",
                "Downtime": None,
                "Line": "Line A",
            },
            {
                "Date": "2026-03-10 01:30:00",
                "Production": "invalid",
                "Defects": "0",
                "Downtime": "0",
                "Line": "Line A",
            },
            {
                "Date": None,
                "Production": None,
                "Defects": None,
                "Downtime": None,
                "Line": None,
            },
        ]
    )

    dataset = normalize_manufacturing_dataframe(
        dataframe,
        source_timezone="America/New_York",
        downtime_unit="seconds",
    )

    assert dataset.records[0].timestamp == datetime(
        2026, 3, 8, 6, 30, tzinfo=timezone.utc
    )
    assert dataset.records[0].downtime_minutes == 60
    assert dataset.records[0].line == "Line A"
    assert dataset.records[1].defects is None
    assert dataset.data_quality.rows_read == 5
    assert dataset.data_quality.empty_rows == 1
    assert dataset.data_quality.duplicate_rows == 1
    assert dataset.data_quality.rejected_rows == 1
    assert dataset.data_quality.invalid_rows == 3
    assert dataset.data_quality.rows_used_for_analysis == 2


@pytest.mark.parametrize(
    (
        "require_production",
        "expected_invalid_rows",
        "expected_rejected_rows",
        "expected_quality_score",
        "expected_record_count",
    ),
    [
        (True, 3, 3, 25, 1),
        (False, 6, 0, 0, 4),
    ],
)
def test_missing_production_is_counted_as_missing_not_invalid(
    require_production,
    expected_invalid_rows,
    expected_rejected_rows,
    expected_quality_score,
    expected_record_count,
):
    dataset = normalize_manufacturing_dataframe(
        pd.DataFrame(
            {
                "Production": [None, "abc", -500, 10],
                "Reference": ["missing", "malformed", "negative", "valid"],
            }
        ),
        require_production=require_production,
    )

    assert dataset.data_quality.missing_values["Production"] == 1
    assert dataset.data_quality.invalid_values.get("Production", 0) == 2
    assert dataset.data_quality.invalid_rows == expected_invalid_rows
    assert dataset.data_quality.rejected_rows == expected_rejected_rows
    assert dataset.data_quality.quality_score == expected_quality_score
    assert len(dataset.records) == expected_record_count


def test_analytics_calculates_weighted_defect_rate_and_links_evidence():
    dataset = normalize_manufacturing_dataframe(
        pd.DataFrame(
            {
                "Production": [100, 300],
                "Defects": [1, 9],
                "Downtime": [10, 30],
                "Line": ["Line A", "Line A"],
            }
        )
    )

    result = analyze_manufacturing_dataset(dataset)
    defect_rate = result["kpi_results"]["defect_rate"]

    assert defect_rate["value"] == 2.5
    assert defect_rate["record_count"] == 2
    assert defect_rate["evidence_id"] in {
        item["id"] for item in result["evidence_references"]
    }


def test_machine_intelligence_calculates_oee_reliability_and_normalizes_events():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            [
                {
                    "Date": "2026-10-01 08:00:00",
                    "Machine": "M-01",
                    "Line": "Line A",
                    "Shift": "Day",
                    "Production": 80,
                    "Defects": 4,
                    "Downtime": 60,
                    "Downtime Type": "Motor fault",
                    "Downtime Planned": "No",
                    "Machine Status": "Failure",
                    "Available Time": 480,
                    "Operating Time": 420,
                    "Standard Cycle Time": 300,
                    "Maintenance Action": "Motor replacement",
                    "Maintenance Outcome": "Passed test",
                }
            ]
        )
    )

    machine = result["by_machine"][0]
    assert machine["availability_percent"] == 87.5
    assert machine["performance_percent"] == 95.2381
    assert machine["quality_percent"] == 95
    assert machine["oee_percent"] == 79.1667
    assert machine["mtbf_hours"] == 7
    assert machine["mttr_minutes"] == 60
    assert result["downtime"]["unplanned_downtime_minutes"] == 60
    assert result["downtime"]["pareto"][0]["reason"] == "Motor fault"
    assert result["downtime"]["normalized_events"][0]["event_type"] == (
        "unplanned_downtime"
    )
    assert result["maintenance_outcomes"][0]["outcome"] == "Passed test"


def test_machine_oee_keeps_missing_components_null_instead_of_guessing():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Machine": ["M-02"],
                "Production": [10],
                "Downtime": [5],
            }
        )
    )

    assert result["by_machine"][0]["availability_percent"] is None
    assert result["by_machine"][0]["performance_percent"] is None
    assert result["by_machine"][0]["quality_percent"] is None
    assert result["by_machine"][0]["oee_percent"] is None
    assert result["machine_count"] == 1


def test_machine_anomaly_screen_flags_outlier_downtime_and_cycle_time():
    frame = pd.DataFrame(
        {
            "Date": [f"2026-10-{day:02d}" for day in range(1, 10)],
            "Machine": ["M-03"] * 9,
            "Production": [10] * 9,
            "Downtime": [5] * 8 + [90],
            "Cycle Time": [30] * 8 + [120],
        }
    )

    result = analyze_machine_intelligence(frame)

    anomaly_types = {item["type"] for item in result["anomalies"]}
    assert "unusual_downtime_duration" in anomaly_types
    assert "unusual_cycle_time" in anomaly_types


def test_repeated_machine_failures_raise_maintenance_priority():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Date": ["2026-10-01", "2026-10-02", "2026-10-03"],
                "Machine": ["M-04"] * 3,
                "Production": [10] * 3,
                "Machine Status": ["Failure"] * 3,
                "Downtime": [10] * 3,
            }
        )
    )

    assert result["maintenance_priorities"][0]["machine"] == "M-04"
    assert result["maintenance_priorities"][0]["risk"] == "high"
    assert result["maintenance_priorities"][0]["recommended_priority"] == "urgent"


def test_failure_count_excludes_downtime_only_rows_and_sensor_faults():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Date": [
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                    "2026-10-05",
                    "2026-10-06",
                ],
                "Machine": [
                    "MCH-01",
                    "MCH-02",
                    "MCH-03",
                    "MCH-03",
                    "MCH-03",
                    "MCH-04",
                ],
                "Production": [100, 100, 100, 100, 100, 100],
                "Downtime": [30, 20, 40, 35, -10, 25],
                "Downtime Planned": ["No", "No", "No", "No", "No", "No"],
                "Machine Status": [
                    "Running",
                    "Running",
                    "Mechanical Failure",
                    "Mechanical Failure",
                    "Mechanical Failure",
                    "Running",
                ],
                "Downtime Type": [
                    "None",
                    "Sensor Fault",
                    "Mechanical Failure",
                    "Mechanical Failure",
                    "Mechanical Failure",
                    "-",
                ],
                "Available Time": [480] * 6,
                "Operating Time": [450, 460, 440, 445, 490, 455],
                "Standard Cycle Time": [300] * 6,
            }
        )
    )

    by_machine = {item["entity"]: item for item in result["by_machine"]}
    assert by_machine["MCH-01"]["failure_count"] == 0
    assert by_machine["MCH-02"]["failure_count"] == 0
    assert by_machine["MCH-03"]["failure_count"] == 2
    assert by_machine["MCH-04"]["failure_count"] == 0


def test_downtime_pareto_moves_none_to_unknown():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Machine": ["MCH-01", "MCH-01"],
                "Downtime": [281, 75],
                "Downtime Planned": ["No", "Yes"],
                "Downtime Type": ["None", "Scheduled Maintenance"],
                "Machine Status": ["Running", "Maintenance"],
            }
        )
    )

    assert result["downtime"]["planned_downtime_minutes"] == 75
    assert result["downtime"]["unplanned_downtime_minutes"] == 281
    assert result["downtime"]["unclassified_downtime_minutes"] == 0
    pareto = {item["reason"]: item for item in result["downtime"]["pareto"]}
    assert "none" not in pareto
    assert pareto["unknown"]["duration_minutes"] == 281
    assert all(
        event["reason"] == "unknown"
        for event in result["downtime"]["normalized_events"]
        if event["source_row"] == 2
    )


def test_performance_decline_requires_at_least_eight_valid_records():
    frame = pd.DataFrame(
        {
            "Date": [f"2026-10-{day:02d}" for day in range(1, 6)],
            "Machine": ["MCH-03"] * 5,
            "Production": [100, 100, 100, 20, 20],
            "Downtime": [10, 10, 10, 40, 40],
            "Machine Status": ["Running"] * 5,
            "Downtime Planned": ["No"] * 5,
            "Available Time": [480] * 5,
            "Operating Time": [470, 470, 470, 440, 440],
            "Standard Cycle Time": [300] * 5,
        }
    )

    result = analyze_machine_intelligence(frame)
    assert not any(
        anomaly["type"] == "performance_decline"
        for anomaly in result["anomalies"]
    )
    assert any(
        "Mẫu Performance chưa đạt 8 quan sát" in limitation
        for limitation in result["analysis_limitations"]
    )


def test_performance_decline_includes_confidence_and_observation_counts():
    frame = pd.DataFrame(
        {
            "Date": [f"2026-10-{day:02d}" for day in range(1, 13)],
            "Machine": ["MCH-03"] * 12,
            "Production": [100] * 6 + [20] * 6,
            "Downtime": [10] * 12,
            "Machine Status": ["Running"] * 12,
            "Available Time": [480] * 12,
            "Operating Time": [470] * 12,
            "Standard Cycle Time": [300] * 12,
        }
    )

    result = analyze_machine_intelligence(frame)
    anomaly = next(
        item
        for item in result["anomalies"]
        if item["type"] == "performance_decline"
    )
    assert anomaly["sample_count"] == 12
    assert anomaly["earlier_sample_count"] == 6
    assert anomaly["later_sample_count"] == 6
    assert anomaly["confidence"] == "high"


def test_maintenance_priority_separates_failure_downtime_and_maintenance_counts():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Machine": ["MCH-01", "MCH-03", "MCH-03"],
                "Production": [100, 100, 100],
                "Downtime": [120, 40, 40],
                "Downtime Planned": ["No", "No", "No"],
                "Machine Status": ["Running", "Mechanical Failure", "Mechanical Failure"],
                "Maintenance Action": ["", "Inspection", "Repair"],
                "Maintenance Outcome": ["", "Passed", "Passed test"],
            }
        )
    )

    priorities = {item["machine"]: item for item in result["maintenance_priorities"]}
    mch01 = priorities["MCH-01"]
    assert mch01["failure_count"] == 0
    assert mch01["downtime_event_count"] == 1
    assert mch01["maintenance_event_count"] == 0

    mch03 = priorities["MCH-03"]
    assert mch03["failure_count"] == 2
    assert mch03["downtime_event_count"] == 2
    assert mch03["maintenance_event_count"] == 2
    assert mch03["maintenance_passed_count"] == 2
    assert mch03["risk"] == "high"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("Completed", "successful"),
        ("  COMPLETED  ", "successful"),
        ("passed", "successful"),
        ("SUCCESS", "successful"),
        ("Fixed", "successful"),
        ("Hoàn thành", "successful"),
        ("Thành công", "successful"),
        ("Đạt", "successful"),
        ("Failed", "failed"),
        ("Unsuccessful", "failed"),
        ("Not Passed", "failed"),
        ("Rejected", "failed"),
        ("Không đạt", "failed"),
        ("Không hoàn thành", "failed"),
        ("Thất bại", "failed"),
        ("Monitoring required", "monitoring"),
        ("Pending", "monitoring"),
        ("Cần theo dõi", "monitoring"),
        ("Đang giám sát", "monitoring"),
        ("Follow-up", "monitoring"),
        ("None", "unknown"),
        ("Unknown", "unknown"),
        ("", "unknown"),
    ],
)
def test_maintenance_outcome_status_normalization(outcome, expected):
    from app.services.machine_intelligence_service import (
        _normalize_maintenance_status,
    )

    assert _normalize_maintenance_status(outcome) == expected


def test_completed_and_monitoring_require_different_maintenance_counts():
    result = analyze_machine_intelligence(
        pd.DataFrame(
            {
                "Machine": ["MCH-01"] * 4,
                "Production": [100] * 4,
                "Downtime": [20, 20, 20, 20],
                "Downtime Planned": ["No"] * 4,
                "Machine Status": ["Mechanical Failure"] * 4,
                "Maintenance Action": [
                    "Repair",
                    "Inspection",
                    "Calibration",
                    "Replacement",
                ],
                "Maintenance Outcome": [
                    "Completed",
                    "MCH-04 Completed",
                    "Monitoring required",
                    "Failed",
                ],
            }
        )
    )

    priority = result["maintenance_priorities"][0]
    assert priority["maintenance_event_count"] == 4
    assert priority["maintenance_passed_count"] == 2
    assert priority["maintenance_failed_count"] == 1
    assert priority["maintenance_monitoring_count"] == 1
    assert priority["maintenance_success_rate_percent"] == 50

    outcomes = {
        item["outcome"]: item["outcome_status"]
        for item in result["maintenance_outcomes"]
    }
    assert outcomes["Completed"] == "successful"
    assert outcomes["MCH-04 Completed"] == "successful"
    assert outcomes["Monitoring required"] == "monitoring"
    assert outcomes["Failed"] == "failed"


def test_analytics_reports_daily_outlier_and_bottleneck_screening_evidence():
    dataframe = pd.DataFrame(
        {
            "Date": [
                f"2026-10-{day:02d}"
                for day in range(1, 10)
            ]
            + ["2026-10-10"],
            "Production": [10] * 9 + [100],
            "Defects": [1] * 10,
            "Downtime": [2] * 10,
            "Line": ["Line A"] * 10,
        }
    )
    dataset = normalize_manufacturing_dataframe(dataframe)

    result = analyze_manufacturing_dataset(dataset)

    assert len(result["time_series"]) == 10
    production_anomaly = next(
        anomaly
        for anomaly in result["detected_anomalies"]
        if anomaly["metric"] == "production"
    )
    assert production_anomaly["observed"] == 100
    assert production_anomaly["baseline_periods"] == 9
    assert production_anomaly["evidence_ids"][0] in {
        item["id"] for item in result["evidence_references"]
    }
    candidate = next(
        item
        for item in result["potential_bottlenecks"]
        if item["dimension"] == "line"
    )
    assert candidate["entity"] == "Line A"
    assert candidate["interpretation"] == (
        "screening_signal_not_confirmed_capacity_constraint"
    )


def test_orchestrator_returns_unified_result_without_requesting_gemini():
    result = orchestrate_manufacturing_analysis(
        pd.DataFrame(
            {
                "Production": [100, 120],
                "Defects": [2, 3],
            }
        ),
        question="Phân tích KPI",
        include_ai=False,
    )

    assert result["schema_version"] == "2.0"
    assert result["request_classification"]["intent"] == "kpi"
    assert result["ai_status"] == "not_requested"
    assert result["kpi_results"]["production"]["value"] == 220
    assert result["data_contract"]["stored_timezone"] == "UTC"
    assert "trend_analysis" not in result["executed_modules"]
    assert result["time_series"] == []
    assert result["detected_anomalies"] == []
    assert result["potential_bottlenecks"] == []


def test_missing_gemini_key_preserves_python_analysis():
    with patch("app.services.intelligence_orchestrator.settings.GEMINI_API_KEY", ""):
        result = orchestrate_manufacturing_analysis(
            pd.DataFrame({"Production": [10]}),
        )

    assert result["ai_status"] == "not_configured"
    assert result["kpi_results"]["production"]["value"] == 10
    assert result["evidence_references"]


def test_ai_interpretation_cannot_reference_unknown_evidence():
    interpretation = EvidenceBoundAIInterpretation(
        executive_summary=EvidenceBoundStatement(
            text="Dữ liệu cho thấy xu hướng ổn định.",
            evidence_ids=["E9999"],
        ),
        root_cause_hypotheses=[],
        recommendations=[],
        limitations=[],
    )

    try:
        _validate_ai_evidence(interpretation, {"E0001"})
    except ValueError as error:
        assert "evidence ID không tồn tại" in str(error)
    else:
        raise AssertionError("Invalid evidence reference should be rejected.")


def test_legacy_hardware_analysis_uses_python_fallback_without_gemini_key():
    not_configured = ValueError(
        "GEMINI_API_KEY chưa được cấu hình."
    )
    with (
        patch.object(hardware_service.settings, "GEMINI_API_KEY", ""),
        patch.object(
            hardware_service,
            "generate_ai_insights",
            side_effect=not_configured,
        ),
        patch.object(
            hardware_service,
            "analyze_hardware_deep",
            side_effect=not_configured,
        ),
    ):
        result = hardware_service.analyze_hardware_dataframe(
            pd.DataFrame({"Production": [10]})
        )

    assert result["ai_status"] == "not_configured"
    assert result["unified_analysis"]["kpi_results"]["production"]["value"] == 10
