import pandas as pd
import pytest

from app.services.analysis_readiness_service import (
    classify_analysis_dataset,
    ensure_analysis_ready,
)
from app.services.hardware_service import analyze_hardware_dataframe
from app.services.intelligence_orchestrator import (
    orchestrate_manufacturing_analysis,
)


def test_classification_separates_topic_detection_from_analysis_readiness():
    classification = classify_analysis_dataset(
        pd.DataFrame({"Machine": ["M-1"]})
    )

    assert classification["domains"] == ["machine"]
    assert not classification["analyses"]["production"]["ready"]
    assert not classification["analyses"]["machine"]["ready"]
    with pytest.raises(ValueError, match="chưa đủ dữ liệu cho phân tích sản xuất"):
        ensure_analysis_ready(classification, "production")


def test_predictive_analysis_requires_a_date_and_a_measurable_field():
    no_date = classify_analysis_dataset(
        pd.DataFrame({"Production": [10]})
    )
    date_only = classify_analysis_dataset(
        pd.DataFrame({"Date": ["2026-03-01"]})
    )

    assert not no_date["analyses"]["predictive"]["ready"]
    assert not date_only["analyses"]["predictive"]["ready"]
    with pytest.raises(ValueError, match="cột Date"):
        ensure_analysis_ready(no_date, "predictive")


def test_unified_analysis_keeps_absent_metrics_null_instead_of_zero():
    result = orchestrate_manufacturing_analysis(
        pd.DataFrame(
            {
                "Date": ["2026-03-01", "2026-03-02"],
                "Defects": [2, 3],
            }
        ),
        include_ai=False,
        require_production=False,
    )

    assert result["kpi_results"]["production"]["value"] is None
    assert result["kpi_results"]["defects"]["value"] == 5
    assert all(item["production"] is None for item in result["time_series"])
    assert any(
        limitation.startswith("Không có cột Production")
        for limitation in result["analysis_limitations"]
    )


def test_general_hardware_analysis_runs_available_domains_without_production():
    result = analyze_hardware_dataframe(
        pd.DataFrame(
            {
                "Date": ["2026-03-01", "2026-03-02"],
                "Material": ["Resin", "Resin"],
                "Material Stock": [10, 8],
                "Material Unit": ["kg", "kg"],
            }
        )
    )

    assert result["title"] == "Phân tích dữ liệu đa lĩnh vực"
    assert result["unified_analysis"]["kpi_results"]["production"]["value"] is None
    assert "material" in result["domain_analyses"]
    assert "predictive" in result["domain_analyses"]
