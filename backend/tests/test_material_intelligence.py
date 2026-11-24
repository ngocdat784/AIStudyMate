from unittest.mock import patch

import pandas as pd

from app.services.manufacturing_data_foundation import (
    normalize_manufacturing_dataframe,
)
from app.services.material_intelligence_service import (
    MaterialAIInterpretation,
    MaterialAIStatement,
    _validate_ai_evidence,
    analyze_material_intelligence,
)


def _material_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "2026-10-01",
                "Material": "Resin",
                "Material Type": "Polymer",
                "Material Lot": "LOT-A",
                "Material Unit": "kg",
                "Planned Consumption": 1,
                "Actual Consumption": 1.2,
                "Material Unit Cost": 10,
                "Currency": "USD",
                "Material Loss": 0.1,
                "Scrap Material": 0.05,
                "Supplier": "Supplier A",
                "Product": "Widget",
                "Process": "Molding",
                "Batch": "PB-01",
                "Production": 100,
                "Defects": 5,
            },
            {
                "Date": "2026-10-02",
                "Material": "Resin",
                "Material Type": "Polymer",
                "Material Lot": "LOT-B",
                "Material Unit": "g",
                "Planned Consumption": 900,
                "Actual Consumption": 800,
                "Material Unit Cost": 0.01,
                "Currency": "USD",
                "Material Loss": 50,
                "Scrap Material": 20,
                "Supplier": "Supplier A",
                "Product": "Widget",
                "Process": "Molding",
                "Batch": "PB-02",
                "Production": 100,
                "Defects": 0,
            },
        ]
    )


def test_normalization_converts_material_units_and_unit_price():
    dataset = normalize_manufacturing_dataframe(
        _material_frame(),
        require_production=False,
    )

    first = dataset.records[0]
    assert first.material_unit == "g"
    assert first.actual_consumption == 1200
    assert first.planned_consumption == 1000
    assert first.material_unit_cost == 0.01
    assert first.material_loss_quantity == 100
    assert first.scrap_material_quantity == 50
    assert first.material_lot == "LOT-A"
    assert first.material_currency == "USD"


def test_material_consumption_yield_cost_traceability_and_supplier_quality():
    result = analyze_material_intelligence(_material_frame())

    assert result["data_structure"]["material_count"] == 1
    assert result["data_structure"]["material_lot_count"] == 2
    material = result["material_performance"][0]
    assert material["actual_consumption"] == 2000
    assert material["planned_consumption"] == 1900
    assert material["consumption_variance"] == 100
    assert material["consumption_variance_percent"] == 5.2632
    assert material["material_usage_rate"] == 10
    assert material["material_loss"] == 150
    assert material["scrap_material"] == 70
    assert material["material_yield_percent"] == 92.5
    assert material["material_cost"] == 20
    assert material["currency"] == "USD"
    assert material["material_defect_rate_percent"] == 2.5
    assert result["process_material_loss"][0]["material_loss"] == 150
    assert result["process_material_loss"][0]["waste_rate_percent"] == 7.5

    lot = next(
        item
        for item in result["defect_associated_lots"]
        if item["material_lot"] == "LOT-A"
    )
    assert lot["supplier"] == "Supplier A"
    assert lot["product"] == "Widget"
    assert lot["process"] == "Molding"
    assert lot["production_batch"] == "PB-01"
    assert lot["defects"] == 5
    assert lot["interpretation"] == "association_only_not_causation"
    assert result["material_product_mapping"][0]["material_lots"]
    assert result["material_product_mapping"][0]["production_batch"] == "PB-01"
    assert result["supplier_quality"][0]["defect_rate_percent"] == 2.5
    assert result["supplier_quality_trend"][0]["period"] == "2026-10"
    assert result["management_answers"]["consumption_over_plan"]["evidence_ids"]


def test_material_ai_is_opt_in_and_missing_inputs_are_not_imputed():
    frame = pd.DataFrame(
        {
            "Material": ["Resin"],
            "Production": [10],
        }
    )
    with patch("app.services.material_intelligence_service.settings.GEMINI_API_KEY", ""):
        result = analyze_material_intelligence(frame, include_ai=True)

    assert result["ai_status"] == "not_configured"
    assert result["kpi_results"]["actual_consumption"]["value"] is None
    assert result["kpi_results"]["material_cost"]["value"] is None
    assert result["analysis_limitations"]


def test_material_ai_rejects_unknown_evidence():
    statement = MaterialAIStatement(text="Kiểm tra lô liên quan.", evidence_ids=["E9999"])
    interpretation = MaterialAIInterpretation(
        executive_summary=statement,
        answers=[
            {
                "topic": topic,
                "text": "Dữ liệu cần được rà soát.",
                "evidence_ids": ["E0001"],
            }
            for topic in (
                "highest_material_loss",
                "defective_lots",
                "unstable_suppliers",
                "consumption_over_plan",
                "production_risk",
            )
        ],
        root_cause_hypotheses=[],
        recommendations=[],
        limitations=[],
    )

    try:
        _validate_ai_evidence(interpretation, {"E0001"})
    except ValueError as error:
        assert "unknown evidence ID" in str(error)
    else:
        raise AssertionError("Invalid evidence reference should be rejected.")


def test_material_totals_do_not_mix_units_or_currencies():
    result = analyze_material_intelligence(
        pd.DataFrame(
            [
                {
                    "Material": "Resin",
                    "Material Unit": "kg",
                    "Actual Consumption": 2,
                    "Material Unit Cost": 4,
                    "Currency": "USD",
                    "Material Loss": 0.1,
                },
                {
                    "Material": "Solvent",
                    "Material Unit": "L",
                    "Actual Consumption": 1,
                    "Material Unit Cost": 3,
                    "Currency": "EUR",
                    "Material Loss": 0.1,
                },
            ]
        )
    )

    assert result["kpi_results"]["actual_consumption"]["value"] is None
    assert result["kpi_results"]["material_loss"]["value"] is None
    assert result["kpi_results"]["material_cost"]["value"] is None
    assert len(result["material_performance"]) == 2


def test_highest_material_loss_is_ranked_within_each_unit_only():
    result = analyze_material_intelligence(
        pd.DataFrame(
            [
                {
                    "Material": "Steel",
                    "Material Unit": "g",
                    "Actual Consumption": 100000,
                    "Material Loss": 44000,
                },
                {
                    "Material": "Plastic",
                    "Material Unit": "g",
                    "Actual Consumption": 50000,
                    "Material Loss": 23000,
                },
                {
                    "Material": "Electronic Component",
                    "Material Unit": "piece",
                    "Actual Consumption": 1000,
                    "Material Loss": 210,
                },
            ]
        )
    )

    answer = result["management_answers"]["highest_material_loss"]
    assert "Không có cơ sở quy đổi" in answer["answer"]
    assert "Steel ghi nhận 44000 g" in answer["answer"]
    assert "Electronic Component ghi nhận 210 piece" in answer["answer"]
    assert "Steel có hao hụt cao nhất trên toàn bộ" not in answer["answer"]

    evidence_ids = set(answer["evidence_ids"])
    assert evidence_ids
    assert evidence_ids.issubset(
        {item["id"] for item in result["evidence_references"]}
    )

    steel = next(
        item
        for item in result["material_performance"]
        if item["material"] == "Steel"
    )
    electronic = next(
        item
        for item in result["material_performance"]
        if item["material"] == "Electronic Component"
    )
    assert steel["evidence_ids"]["material_loss"] in evidence_ids
    assert electronic["evidence_ids"]["material_loss"] in evidence_ids


def test_highest_material_loss_uses_single_unit_as_direct_comparison():
    result = analyze_material_intelligence(
        pd.DataFrame(
            [
                {
                    "Material": "Steel",
                    "Material Unit": "g",
                    "Actual Consumption": 100000,
                    "Material Loss": 44000,
                },
                {
                    "Material": "Plastic",
                    "Material Unit": "g",
                    "Actual Consumption": 50000,
                    "Material Loss": 23000,
                },
            ]
        )
    )

    answer = result["management_answers"]["highest_material_loss"]
    assert "cùng đơn vị g" in answer["answer"]
    assert "Steel" in answer["answer"]
