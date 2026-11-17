from datetime import datetime, timezone

from unittest.mock import patch

import pandas as pd

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
