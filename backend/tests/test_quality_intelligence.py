import random
from unittest.mock import patch

import pandas as pd

from app.services.manufacturing_data_foundation import (
    normalize_manufacturing_dataframe,
)
from app.services.quality_intelligence_service import (
    QualityAIAnswer,
    QualityAIInterpretation,
    QualityAIStatement,
    _ai_validation,
    analyze_quality_intelligence,
)


def _quality_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "2026-11-01",
                "Product": "Widget",
                "Line": "Line A",
                "Shift": "Day",
                "Process": "Cut",
                "Process Sequence": 1,
                "Batch": "B-1",
                "Inspected": 100,
                "Defective Units": 8,
                "Defects": 10,
                "First Pass Inspected": 100,
                "First Pass Passed": 90,
                "Reworked Units": 5,
                "Scrapped Units": 3,
                "Defect Type": "Scratch",
            },
            {
                "Date": "2026-11-01",
                "Product": "Widget",
                "Line": "Line A",
                "Shift": "Day",
                "Process": "Pack",
                "Process Sequence": 2,
                "Batch": "B-1",
                "Inspected": 90,
                "Defective Units": 4,
                "Defects": 5,
                "First Pass Inspected": 90,
                "First Pass Passed": 81,
                "Reworked Units": 2,
                "Scrapped Units": 1,
                "Defect Type": "Crack",
            },
        ]
    )


def test_quality_kpis_use_explicit_counts_and_ordered_process_yield():
    result = analyze_quality_intelligence(_quality_frame(), include_ai=False)
    kpis = result["kpi_results"]

    assert kpis["total_inspected"]["value"] == 190
    assert kpis["total_defects"]["value"] == 15
    assert kpis["defect_rate"]["value"] == 6.3158
    assert kpis["fpy"]["value"] == 90
    assert kpis["rty"]["value"] == 81
    assert kpis["rework_rate"]["value"] == 3.6842
    assert kpis["scrap_rate"]["value"] == 2.1053
    assert result["defect_pareto"][0]["category"] == "Scratch"
    assert result["defect_pareto"][0]["share_percent"] == 66.6667
    assert result["quality_by_process"][0]["process"] == "Cut"
    assert all(metric["evidence_id"] for metric in kpis.values())


def test_quality_kpis_remain_null_without_complete_explicit_denominators():
    result = analyze_quality_intelligence(
        pd.DataFrame(
            {
                "Production": [10, 20],
                "Inspected": [10, 20],
                "Defects": [1, None],
                "Rework": [1, None],
            }
        ),
        include_ai=False,
    )

    assert result["kpi_results"]["total_inspected"]["value"] == 30
    assert result["kpi_results"]["defect_rate"]["value"] is None
    assert result["kpi_results"]["fpy"]["value"] is None
    assert result["kpi_results"]["rty"]["value"] is None
    assert result["kpi_results"]["rework_rate"]["value"] is None
    assert result["kpi_results"]["scrap_rate"]["value"] is None


def test_defect_rate_does_not_mix_defective_units_and_defect_events():
    result = analyze_quality_intelligence(
        pd.DataFrame(
            {
                "Inspected": [100, 100],
                "Defective Units": [8, None],
                "Defects": [10, 5],
            }
        ),
        include_ai=False,
    )

    assert result["kpi_results"]["defect_rate"]["value"] is None
    assert (
        result["kpi_results"]["defect_rate"]["basis"]
        == "incomplete_defective_units_coverage"
    )


def test_fpy_requires_complete_process_sequence_coverage():
    result = analyze_quality_intelligence(
        pd.DataFrame(
            {
                "Inspected": [100, 100],
                "Process Sequence": [1, None],
                "First Pass Inspected": [100, 100],
                "First Pass Passed": [90, 95],
            }
        ),
        include_ai=False,
    )

    assert result["kpi_results"]["fpy"]["value"] is None
    assert (
        result["kpi_results"]["fpy"]["basis"]
        == "incomplete_process_sequence_coverage"
    )


def test_quality_aliases_normalize_and_invalid_counts_are_reported():
    dataset = normalize_manufacturing_dataframe(
        pd.DataFrame(
            {
                "Actual Output": [10, 12],
                "Inspected Qty": [10, 12],
                "Defective Units": [11, 2],
                "Measurement Value": [-0.5, 0.5],
                "LSL": [-1, -1],
                "USL": [1, 1],
                "Characteristic": ["Diameter", "Diameter"],
                "Subgroup": ["S1", "S1"],
            }
        )
    )

    assert dataset.records[0].inspected_quantity == 10
    assert dataset.records[0].measurement_value == -0.5
    assert dataset.records[0].lower_spec_limit == -1
    assert dataset.records[0].measurement_name == "Diameter"
    assert dataset.records[0].subgroup_id == "S1"
    assert dataset.records[0].defective_units is None
    assert dataset.data_quality.invalid_values["Defective Units"] == 1


def test_p_c_u_charts_and_iqr_detect_a_known_quality_spike():
    frame = pd.DataFrame(
        [
            {
                "Date": f"2026-10-{day:02d}",
                "Product": "Widget",
                "Line": "Line A",
                "Process": "Seal",
                "Inspected": 100,
                "Defective Units": 30 if day == 20 else 1,
                "Defects": 30 if day == 20 else 1,
            }
            for day in range(1, 21)
        ]
    )

    result = analyze_quality_intelligence(frame, include_ai=False)
    spc = result["spc"]

    assert spc["charts"]["p"][0]["stability"] == "out_of_control"
    assert spc["charts"]["c"][0]["stability"] == "out_of_control"
    assert spc["charts"]["u"][0]["stability"] == "out_of_control"
    assert spc["process_stability"]["status"] == "out_of_control"
    assert any(
        item["type"] == "defect_rate_increase"
        for item in result["quality_anomalies"]
    )
    assert result["quality_anomalies"]


def test_xbar_r_capability_requires_stable_subgroups_and_normality():
    generator = random.Random(0)
    frame = pd.DataFrame(
        [
            {
                "Production": 1,
                "Measurement Value": 10 + generator.gauss(0, 0.3),
                "Characteristic": "Diameter",
                "Subgroup": f"S{index // 5:02d}",
                "LSL": 8,
                "USL": 12,
            }
            for index in range(100)
        ]
    )

    spc = analyze_quality_intelligence(frame, include_ai=False)["spc"]
    charts = spc["charts"]["x_bar_r"][0]

    assert charts["x_bar"]["stability"] == "stable"
    assert charts["r"]["stability"] == "stable"
    assert spc["process_stability"]["status"] == "stable"
    capability = spc["capability"][0]
    assert capability["status"] == "available"
    assert capability["cp"] > 0
    assert capability["cpk"] > 0


def test_missing_gemini_key_keeps_python_quality_results():
    with patch(
        "app.services.quality_intelligence_service.settings.GEMINI_API_KEY",
        "",
    ):
        result = analyze_quality_intelligence(_quality_frame())

    assert result["ai_status"] == "not_configured"
    assert result["kpi_results"]["total_defects"]["value"] == 15
    assert result["evidence_references"]


def test_quality_ai_rejects_unknown_evidence_and_unlinked_numbers():
    answers = [
        QualityAIAnswer(
            topic=topic,
            text="Cần tiếp tục theo dõi.",
            evidence_ids=["E0001"],
        )
        for topic in (
            "top_defect",
            "highest_defect_process",
            "quality_trend",
            "risky_products",
            "statistical_control",
        )
    ]
    valid_shape = QualityAIInterpretation(
        executive_summary=QualityAIStatement(
            text="Chất lượng cần được theo dõi.",
            evidence_ids=["E0001"],
        ),
        answers=answers,
        root_cause_hypotheses=[],
        recommendations=[],
        limitations=[],
    )
    _ai_validation(valid_shape, {"E0001"})

    invalid = valid_shape.model_copy(
        update={
            "executive_summary": QualityAIStatement(
                text="Defect rate là 12%.",
                evidence_ids=["E0001"],
            )
        }
    )
    try:
        _ai_validation(invalid, {"E0001"})
    except ValueError as error:
        assert "numeric claims" in str(error)
    else:
        raise AssertionError("Numeric AI claims must be rejected.")
