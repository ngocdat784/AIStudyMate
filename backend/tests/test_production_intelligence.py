from unittest.mock import patch

import pandas as pd

from app.services.production_intelligence_service import (
    ProductionAIAnswer,
    ProductionAIInterpretation,
    ProductionAIStatement,
    _validate_ai_interpretation,
    analyze_production_intelligence,
)


def _production_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "2026-10-01",
                "Product": "Product A",
                "Line": "Line A",
                "Shift": "Day",
                "Process": "Cut",
                "Production": 90,
                "Planned Production": 100,
                "Cycle Time": 30,
                "Standard Cycle Time": 40,
                "Available Time": 480,
                "Operating Time": 450,
                "Waiting Time": 30,
            },
            {
                "Date": "2026-10-02",
                "Product": "Product A",
                "Line": "Line A",
                "Shift": "Night",
                "Process": "Pack",
                "Production": 80,
                "Planned Production": 100,
                "Cycle Time": 40,
                "Standard Cycle Time": 30,
                "Available Time": 480,
                "Operating Time": 400,
                "Waiting Time": 80,
            },
            {
                "Date": "2026-10-03",
                "Product": "Product B",
                "Line": "Line B",
                "Shift": "Day",
                "Process": "Cut",
                "Production": 120,
                "Planned Production": 120,
                "Cycle Time": 35,
                "Standard Cycle Time": 30,
                "Available Time": 480,
                "Operating Time": 420,
                "Waiting Time": 60,
            },
            {
                "Date": "2026-10-04",
                "Product": "Product B",
                "Line": "Line B",
                "Shift": "Night",
                "Process": "Pack",
                "Production": 100,
                "Planned Production": 100,
                "Cycle Time": 30,
                "Standard Cycle Time": 30,
                "Available Time": 480,
                "Operating Time": 450,
                "Waiting Time": 30,
            },
        ]
    )


def test_production_kpis_use_explicit_plan_and_time_denominators():
    result = analyze_production_intelligence(
        _production_frame(),
        include_ai=False,
    )
    kpis = result["kpi_results"]

    assert kpis["planned_production"]["value"] == 420
    assert kpis["actual_production"]["value"] == 390
    assert kpis["production_achievement_rate"]["value"] == 92.8571
    assert kpis["plan_variance"]["value"] == -30
    assert kpis["production_rate"]["value"] == 12.1875
    assert kpis["throughput"]["value"] == 13.6047
    assert kpis["cycle_time"]["value"] == 33.5897
    assert kpis["takt_time"]["value"] == 274.2857
    assert kpis["line_efficiency"]["value"] == 10.9375
    assert kpis["line_utilization"]["value"] == 89.5833
    assert kpis["waiting_time"]["value"] == 200
    assert all(metric["evidence_id"] for metric in kpis.values())


def test_shift_line_period_and_bottleneck_rankings_match_known_data():
    result = analyze_production_intelligence(
        _production_frame(),
        include_ai=False,
    )

    assert len(result["line_performance"]) == 2
    assert result["management_answers"]["weakest_line"]["answer"].startswith(
        "Line A"
    )
    assert result["management_answers"]["best_shift"]["answer"].startswith(
        "Day"
    )
    assert result["bottleneck_analysis"]["candidates"][0]["process"] == "Pack"
    assert result["production_trends"]["daily"][0]["actual_production"] == 90
    assert len(result["production_trends"]["weekly"]) == 1
    assert len(result["production_trends"]["monthly"]) == 1
    assert result["plan_vs_actual"]["variance"]["value"] == -30
    assert result["production_anomalies"]


def test_missing_plan_and_time_inputs_return_null_for_unsupported_kpis():
    result = analyze_production_intelligence(
        pd.DataFrame({"Production": [10, 12]}),
        include_ai=False,
    )

    assert result["kpi_results"]["production_achievement_rate"]["value"] is None
    assert result["kpi_results"]["production_rate"]["value"] is None
    assert result["kpi_results"]["throughput"]["value"] is None
    assert result["kpi_results"]["cycle_time"]["value"] is None
    assert result["kpi_results"]["takt_time"]["value"] is None
    assert result["kpi_results"]["line_efficiency"]["value"] is None
    assert result["kpi_results"]["line_utilization"]["value"] is None
    assert result["ai_status"] == "not_requested"
    assert result["analysis_limitations"]


def test_time_fields_normalize_timezone_and_units():
    frame = pd.DataFrame(
        {
            "Actual Production": [100],
            "Plan": [120],
            "Production Start": ["2026-10-01 22:00"],
            "Production End": ["2026-10-02 06:00"],
            "Cycle Time": [1],
            "Standard Cycle Time": [2],
            "Available Time": [8],
            "Operating Time": [7],
            "Waiting Time": [1],
        }
    )
    result = analyze_production_intelligence(
        frame,
        source_timezone="Asia/Ho_Chi_Minh",
        cycle_time_unit="minutes",
        duration_unit="hours",
        include_ai=False,
    )
    data_contract = result["data_contract"]
    record = result["data_structure"]

    assert data_contract["column_mapping"]["Actual Production"] == "Production"
    assert data_contract["units"]["cycle_time"] == "seconds"
    assert record["production_interval_count"] == 1
    assert result["kpi_results"]["production_rate"]["value"] == 12.5
    assert result["kpi_results"]["throughput"]["value"] == 14.2857
    assert result["kpi_results"]["cycle_time"]["value"] == 60
    assert result["kpi_results"]["takt_time"]["value"] == 240


def test_daily_trend_groups_by_source_timezone_not_utc_date():
    frame = pd.DataFrame(
        {
            "Date": ["2026-10-01 00:30", "2026-10-01 23:00"],
            "Production": [10, 20],
        }
    )

    daily = analyze_production_intelligence(
        frame,
        source_timezone="Asia/Ho_Chi_Minh",
        include_ai=False,
    )["production_trends"]["daily"]

    assert len(daily) == 1
    assert daily[0]["period"] == "2026-10-01"
    assert daily[0]["actual_production"] == 30


def test_repeated_shift_duration_is_counted_once_for_time_based_kpis():
    frame = pd.DataFrame(
        [
            {
                "Date": "2026-10-01",
                "Line": "Line A",
                "Shift": "Day",
                "Production": 40,
                "Planned Production": 80,
                "Available Time": 480,
                "Operating Time": 450,
                "Waiting Time": 30,
            },
            {
                "Date": "2026-10-01",
                "Line": "Line A",
                "Shift": "Day",
                "Production": 60,
                "Planned Production": 120,
                "Available Time": 480,
                "Operating Time": 450,
                "Waiting Time": 30,
            },
        ]
    )

    kpis = analyze_production_intelligence(
        frame,
        include_ai=False,
    )["kpi_results"]

    assert kpis["production_rate"]["value"] == 12.5
    assert kpis["throughput"]["value"] == 13.3333
    assert kpis["takt_time"]["value"] == 144
    assert kpis["line_utilization"]["value"] == 93.75
    assert kpis["waiting_time"]["value"] == 30


def test_missing_gemini_key_keeps_python_management_answers():
    with patch(
        "app.services.production_intelligence_service.settings.GEMINI_API_KEY",
        "",
    ):
        result = analyze_production_intelligence(_production_frame())

    assert result["ai_status"] == "not_configured"
    assert result["kpi_results"]["actual_production"]["value"] == 390
    assert result["management_answers"]["weakest_line"] is not None
    assert result["evidence_references"]


def test_cycle_time_anomaly_uses_eight_prior_observations():
    frame = pd.DataFrame(
        {
            "Date": [f"2026-10-{day:02d}" for day in range(1, 11)],
            "Production": [100] * 10,
            "Cycle Time": [30] * 9 + [100],
        }
    )

    result = analyze_production_intelligence(frame, include_ai=False)

    cycle_anomaly = next(
        item
        for item in result["production_anomalies"]
        if item["type"] == "cycle_time_increase"
    )
    assert cycle_anomaly["observed_seconds"] == 100
    assert cycle_anomaly["severity"] == "high"
    assert cycle_anomaly["evidence_ids"][0] in {
        evidence["id"] for evidence in result["evidence_references"]
    }


def test_ai_interpretation_rejects_unlinked_evidence_and_numeric_claims():
    statement = ProductionAIStatement(
        text="Sản lượng đang giảm.",
        evidence_ids=["E9999"],
    )
    interpretation = ProductionAIInterpretation(
        executive_summary=statement,
        answers=[
            ProductionAIAnswer(
                topic=topic,
                text="Cần kiểm tra thêm.",
                evidence_ids=["E0001"],
            )
            for topic in (
                "weakest_line",
                "plan_shortfall",
                "best_shift",
                "bottleneck_process",
                "production_decline",
            )
        ],
        root_cause_hypotheses=[],
        recommendations=[],
        limitations=[],
    )
    try:
        _validate_ai_interpretation(interpretation, {"E0001"})
    except ValueError as error:
        assert "unknown evidence ID" in str(error)
    else:
        raise AssertionError("Unknown evidence references must be rejected.")

    numeric = ProductionAIInterpretation(
        executive_summary=ProductionAIStatement(
            text="Sản lượng là 100.",
            evidence_ids=["E0001"],
        ),
        answers=interpretation.answers,
        root_cause_hypotheses=[],
        recommendations=[],
        limitations=[],
    )
    try:
        _validate_ai_interpretation(numeric, {"E0001"})
    except ValueError as error:
        assert "numeric results" in str(error)
    else:
        raise AssertionError("Unreferenced numeric claims must be rejected.")
