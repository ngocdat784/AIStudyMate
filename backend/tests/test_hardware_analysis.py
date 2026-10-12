import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pydantic import ValidationError

from app.services import hardware_service
from app.services.hardware_ai_service import (
    DeepAIAnalysis,
    RecommendedAction,
    RiskArea,
    _build_ai_context,
    _build_prompt,
    _find_entity_row,
    _get_entities,
    _normalize_risk_levels,
    _normalize_recommendation_priorities,
    _gemini_response_schema,
    _validate_risk_areas,
    enrich_risk_areas,
    _normalize_quality_observations,
    normalize_summary_record_scope,
    settings as hardware_ai_service_settings,
)


class HardwareAnalysisTests(unittest.TestCase):
    def test_deep_analysis_schema_rejects_unexpected_fields(self):
        with self.assertRaises(ValidationError):
            DeepAIAnalysis.model_validate(
                {
                    "executive_summary": {"summary": "Tóm tắt."},
                    "production_analysis": {"summary": "Sản xuất."},
                    "quality_analysis": {"summary": "Chất lượng."},
                    "component_analysis": {"summary": "Linh kiện."},
                    "supplier_analysis": {"summary": "Nhà cung cấp."},
                    "batch_analysis": {"summary": "Lô sản xuất."},
                    "downtime_analysis": {"summary": "Thời gian dừng."},
                    "untrusted_metric": 999999,
                }
            )

    def test_gemini_response_schema_omits_unsupported_additional_properties(self):
        def contains_additional_properties(value):
            if isinstance(value, dict):
                return "additionalProperties" in value or any(
                    contains_additional_properties(item)
                    for item in value.values()
                )
            if isinstance(value, list):
                return any(
                    contains_additional_properties(item)
                    for item in value
                )
            return False

        self.assertFalse(
            contains_additional_properties(
                _gemini_response_schema(DeepAIAnalysis)
            )
        )

    def test_complete_dataset_produces_all_analysis_groups_and_extremes(self):
        dataframe = pd.DataFrame(
            {
                "Product": ["Widget A", "Widget B"],
                "Model": ["M1", "M2"],
                "Component": ["GPU", "Memory"],
                "Supplier": ["Supplier X", "Supplier Y"],
                "Batch": ["B1", "B2"],
                "Defect Type": ["Scratch", "No power"],
                "Production": [100, 50],
                "Defects": [10, 2],
                "Downtime": [4, 8],
                "Date": ["2026-01-01", "2026-01-02"],
            }
        )

        kpis = hardware_service.calculate_hardware_kpis(dataframe)
        context = _build_ai_context(kpis)

        for analysis_key in (
            "daily_production",
            "product_performance",
            "model_performance",
            "component_performance",
            "supplier_performance",
            "batch_performance",
            "defect_type_analysis",
        ):
            self.assertTrue(context[analysis_key])

        self.assertEqual(
            context["highest_defect_product"]["product"],
            "Widget A",
        )
        self.assertEqual(
            context["highest_downtime_product"]["product"],
            "Widget B",
        )
        self.assertIn("quality_score", context["data_quality"])
        self.assertEqual(
            context["measurement_units"]["quality_score"],
            "điểm trên thang 100",
        )
        self.assertIn("rows_used_for_kpi", context["data_processing"])
        self.assertEqual(context["measurement_units"]["defect_rate"], "%")
        self.assertEqual(context["measurement_units"]["downtime"], "phút")

    def test_data_quality_and_kpi_processing_keep_distinct_row_counts(self):
        rows = []
        for index in range(11):
            rows.append(
                {
                    "Product": f"Product {index}",
                    "Model": f"Model {index}",
                    "Component": f"Component {index}",
                    "Supplier": f"Supplier {index}",
                    "Batch": f"Batch {index}",
                    "Production": 100,
                    "Defects": 2,
                    "Downtime": 1,
                    "Date": f"2026-01-{index + 1:02d}",
                }
            )

        rows.append(
            {
                "Product": "Product with missing component",
                "Model": "Model with missing component",
                "Component": None,
                "Supplier": "Supplier with missing component",
                "Batch": "Batch with missing component",
                "Production": 100,
                "Defects": 2,
                "Downtime": 1,
                "Date": "2026-01-12",
            }
        )

        for index in range(6):
            rows.append(
                {
                    "Product": f"Invalid product {index}",
                    "Model": f"Invalid model {index}",
                    "Component": None,
                    "Supplier": f"Invalid supplier {index}",
                    "Batch": f"Invalid batch {index}",
                    "Production": "not numeric",
                    "Defects": 2,
                    "Downtime": 1,
                    "Date": f"2026-02-{index + 1:02d}",
                }
            )

        rows.append(rows[0].copy())
        kpis = hardware_service.calculate_hardware_kpis(
            pd.DataFrame(rows)
        )

        self.assertEqual(kpis["data_quality"]["valid_rows"], 11)
        self.assertEqual(kpis["data_quality"]["invalid_rows"], 8)
        self.assertEqual(
            kpis["data_processing"]["rows_after_deduplication"],
            18,
        )
        self.assertEqual(kpis["data_processing"]["rows_used_for_kpi"], 12)
        self.assertEqual(
            kpis["data_processing"]["invalid_metric_rows_excluded"],
            6,
        )
        self.assertEqual(
            kpis["data_processing"][
                "invalid_metric_rows_excluded_by_metric"
            ],
            {"production": 6, "defects": 0, "downtime": 0},
        )
        self.assertEqual(kpis["component_count"], 11)
        self.assertTrue(
            any(
                row["component"] == "Không xác định"
                for row in kpis["component_performance"]
            )
        )

    def test_component_count_excludes_unidentified_group(self):
        dataframe = pd.DataFrame(
            {
                "Product": ["P1", "P2", "P3", "P4"],
                "Model": ["M1", "M2", "M3", "M4"],
                "Component": ["GPU", "RAM", "Không xác định", None],
                "Supplier": ["S1", "S2", "S3", "S4"],
                "Batch": ["B1", "B2", "B3", "B4"],
                "Production": [100, 100, 100, 100],
                "Defects": [1, 1, 1, 1],
                "Downtime": [0, 0, 0, 0],
                "Date": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                    "2026-01-04",
                ],
            }
        )

        kpis = hardware_service.calculate_hardware_kpis(dataframe)

        self.assertEqual(kpis["component_count"], 2)
        self.assertTrue(
            any(
                row["component"] == "Không xác định"
                for row in kpis["component_performance"]
            )
        )

    def test_generated_kpi_units_and_descriptions_are_normalized(self):
        source_kpis = {
            "total_production": 13000,
            "total_defects": 231,
            "defect_rate": 1.78,
            "total_downtime": 413,
            "component_count": 4,
        }
        gemini_response = {
            "title": "Hardware report",
            "summary": "Summary",
            "kpis": [
                {
                    "name": "Tổng sản lượng",
                    "value": 99999,
                    "unit": "units",
                    "description": "Tổng sản lượng từ bản ghi hợp lệ.",
                },
                {
                    "name": "total_defects",
                    "value": 999,
                    "unit": "count",
                    "description": "AI-generated.",
                },
                {
                    "name": "Tỷ lệ lỗi tổng thể",
                    "value": 99,
                    "unit": "",
                    "description": "AI-generated.",
                },
                {
                    "name": "Tổng downtime",
                    "value": 6.88,
                    "unit": "giờ",
                    "description": "Time in hours",
                },
                {
                    "name": "Tỷ lệ lỗi tổng thể",
                    "value": 2.05,
                    "unit": "",
                    "description": "Rate",
                },
                {
                    "name": "component_count",
                    "value": 5,
                    "unit": "nhóm",
                    "description": "All groups",
                },
            ],
            "insights": ["Insight"],
            "warnings": [],
            "recommendations": ["Action"],
        }
        client = Mock()
        client.interactions.create.return_value = Mock(
            output_text=json.dumps(gemini_response, ensure_ascii=False)
        )

        with patch.object(
            hardware_service,
            "get_client",
            return_value=client,
        ):
            analysis = hardware_service.generate_ai_insights(source_kpis)

        production, defects, overall_defect_rate, downtime, defect_rate, component_count = analysis.kpis
        self.assertEqual(production.value, 13000)
        self.assertEqual(
            production.description,
            "Tổng sản lượng ghi nhận từ các bản ghi được sử dụng để tính KPI.",
        )
        self.assertEqual(defects.value, 231)
        self.assertEqual(
            defects.description,
            "Tổng số lỗi ghi nhận từ các bản ghi được sử dụng để tính KPI.",
        )
        self.assertEqual(overall_defect_rate.value, 1.78)
        self.assertEqual(overall_defect_rate.unit, "%")
        self.assertEqual(downtime.value, 413)
        self.assertEqual(downtime.unit, "phút")
        self.assertEqual(
            downtime.description,
            "Tổng thời gian ngừng máy ghi nhận được, tính bằng phút.",
        )
        self.assertEqual(defect_rate.unit, "%")
        self.assertEqual(component_count.value, 4)
        self.assertEqual(component_count.unit, "loại")
        self.assertEqual(
            component_count.description,
            "Số lượng loại linh kiện xác định được trong dữ liệu, "
            "không bao gồm nhóm chưa xác định.",
        )
        prompt = client.interactions.create.call_args.kwargs["input"]
        self.assertIn("All supplied downtime values are in minutes", prompt)
        self.assertIn("2.05 means", prompt)
        self.assertIn("2.05%, not a unitless value", prompt)
        self.assertIn("A row missing Component may", prompt)
        self.assertNotIn(
            "additionalProperties",
            json.dumps(
                client.interactions.create.call_args.kwargs[
                    "response_format"
                ]["schema"]
            ),
        )

    def test_quality_observations_use_backend_row_counts_and_exclusion_reasons(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={
                "summary": "Chất lượng.",
                "key_observations": [
                    "11 dòng hợp lệ, 8 dòng không hợp lệ và 1 dòng trùng lặp."
                ],
            },
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
        )
        report = {
            "data_quality": {
                "total_rows": 19,
                "data_rows": 19,
                "valid_rows": 11,
                "invalid_rows": 8,
                "empty_rows": 0,
                "duplicate_rows": 1,
            },
            "data_processing": {
                "invalid_metric_rows_excluded": 6,
                "invalid_metric_rows_excluded_by_metric": {
                    "production": 6,
                    "defects": 1,
                    "downtime": 0,
                },
            },
        }

        normalized = _normalize_quality_observations(analysis, report)

        self.assertEqual(
            normalized.quality_analysis.key_observations[0],
            "Trong 19 dòng dữ liệu ban đầu có 11 dòng được đánh giá hợp lệ "
            "và 8 dòng không hợp lệ, trong đó có 1 dòng trùng lặp được "
            "tính vào nhóm không hợp lệ.",
        )
        self.assertEqual(len(normalized.quality_analysis.key_observations), 2)
        self.assertEqual(
            normalized.quality_analysis.key_observations[1],
            "Có 6 dòng bị loại khỏi tính KPI; nguyên nhân theo metric: "
            "Production không hợp lệ ở 6 dòng, Defects không hợp lệ ở "
            "1 dòng. Một dòng có thể xuất hiện ở nhiều nhóm nguyên nhân.",
        )

    def test_deep_prompt_requires_percentages_and_explains_row_counts(self):
        prompt = _build_prompt(
            _build_ai_context(
                {
                    "data_quality": {
                        "valid_rows": 11,
                        "invalid_rows": 8,
                    },
                    "data_processing": {
                        "rows_after_deduplication": 18,
                        "rows_used_for_kpi": 12,
                        "invalid_metric_rows_excluded": 6,
                    },
                }
            )
        )

        self.assertIn("2.05 trong dữ liệu phải viết là 2,05%", prompt)
        self.assertIn("Mọi giá trị downtime trong dữ liệu được tính bằng phút", prompt)
        self.assertIn("rows_used_for_kpi có thể lớn hơn valid_rows", prompt)

    def test_summary_calls_kpi_rows_used_not_universally_valid(self):
        summary = "Dữ liệu gồm 12 bản ghi hợp lệ."
        report = {
            "total_production": 13000,
            "total_defects": 231,
            "defect_rate": 1.78,
            "total_downtime": 413,
            "data_quality": {
                "quality_score": 57.9,
                "quality_status": "poor",
            },
            "data_processing": {"rows_used_for_kpi": 12},
            "daily_production": [
                {"date": "2026-10-01"},
                {"date": "2026-10-02"},
                {"date": "2026-10-03"},
                {"date": "2026-10-04"},
                {"date": "2026-10-05"},
            ],
        }

        normalized = normalize_summary_record_scope(summary, report)

        self.assertEqual(
            normalized,
            "Dữ liệu sản xuất được ghi nhận trong 5 ngày, từ 2026-10-01 "
            "đến 2026-10-05, với 12 bản ghi được sử dụng để tính KPI. "
            "Tổng sản lượng đạt 13.000 đơn vị, tổng số lỗi là 231, "
            "tương ứng tỷ lệ lỗi tổng thể 1,78%. Tổng thời gian ngừng "
            "máy đạt 413 phút. Chất lượng dữ liệu đầu vào đạt 57,9/100, "
            "được xếp loại kém; cần ưu tiên làm sạch và xác thực dữ liệu "
            "trước khi sử dụng cho các phân tích chuyên sâu.",
        )
        self.assertNotIn("12 bản ghi hợp lệ", normalized)
        self.assertEqual(normalized.count("Dữ liệu sản xuất"), 1)
        self.assertNotIn("57,9%", normalized)

    def test_summary_is_not_prefixed_with_duplicate_scope_sentence(self):
        report = {
            "data_processing": {"rows_used_for_kpi": 12},
            "daily_production": [
                {"date": "2026-10-01"},
                {"date": "2026-10-02"},
            ],
        }
        generated = (
            "Dữ liệu bao gồm 12 bản ghi được sử dụng để tính KPI, trải "
            "dài trong 2 ngày sản xuất. Có 12 bản ghi hợp lệ."
        )

        normalized = normalize_summary_record_scope(generated, report)

        self.assertEqual(
            normalized,
            "Dữ liệu sản xuất được ghi nhận trong 2 ngày, từ 2026-10-01 "
            "đến 2026-10-02, với 12 bản ghi được sử dụng để tính KPI.",
        )

    def test_unknown_defect_type_aggregates_defect_quantity_not_rows(self):
        dataframe = pd.DataFrame(
            {
                "Product": ["Widget A", "Widget B"],
                "Model": ["M1", "M2"],
                "Component": ["GPU", "RAM"],
                "Supplier": ["Supplier X", "Supplier Y"],
                "Batch": ["B1", "B2"],
                "Defect Type": [None, "Scratch"],
                "Production": [100, 200],
                "Defects": [8, 3],
                "Downtime": [1, 2],
                "Date": ["2026-10-01", "2026-10-02"],
            }
        )

        kpis = hardware_service.calculate_hardware_kpis(dataframe)
        unknown_group = next(
            row
            for row in kpis["defect_type_analysis"]
            if row["defect_type"] == "Không xác định"
        )

        self.assertEqual(unknown_group["defects"], 8)
        self.assertEqual(
            sum(row["defects"] for row in kpis["defect_type_analysis"]),
            kpis["total_defects"],
        )
        self.assertEqual(kpis["data_quality"]["invalid_rows"], 0)
        self.assertEqual(
            kpis["data_processing"]["invalid_metric_rows_excluded"],
            0,
        )
        prompt = _build_prompt(_build_ai_context(kpis))
        self.assertIn(
            "đại diện cho số lỗi có loại lỗi bị thiếu/trống",
            prompt,
        )
        self.assertIn(
            "không phải số dòng thiếu dữ liệu, số dòng invalid",
            prompt,
        )

    def test_recommendation_priority_uses_configured_thresholds(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            recommended_actions=[
                RecommendedAction(
                    priority="low",
                    action="Làm sạch dữ liệu thiếu Component.",
                    target_type="data",
                    target_name="input",
                    reason="Điểm chất lượng dữ liệu thấp.",
                ),
                RecommendedAction(
                    priority="critical",
                    action="Điều tra chất lượng GPU.",
                    target_type="component",
                    target_name="GPU",
                    reason="Tỷ lệ lỗi vượt mục tiêu.",
                ),
                RecommendedAction(
                    priority="low",
                    action="Kiểm tra downtime.",
                    target_type="operation",
                    target_name="factory",
                    reason="Tổng downtime vượt mục tiêu.",
                ),
            ],
        )
        report = {
            "data_quality": {"quality_score": 70},
            "defect_rate": 2.05,
            "total_downtime": 413,
            "component_performance": [
                {
                    "component": "GPU",
                    "defect_rate": 2.05,
                    "downtime": 100,
                }
            ],
        }

        with (
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DATA_QUALITY_MIN_SCORE",
                90,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DEFECT_RATE_TARGET",
                1.5,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DOWNTIME_TARGET_MINUTES",
                400,
            ),
        ):
            normalized = _normalize_recommendation_priorities(
                analysis,
                report,
            )

        self.assertEqual(
            [item.priority for item in normalized.recommended_actions],
            ["high", "high", "high"],
        )

    def test_group_recommendation_uses_its_own_metric_for_priority(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            recommended_actions=[
                RecommendedAction(
                    priority="high",
                    action="Điều tra chất lượng GPU.",
                    target_type="component",
                    target_name="GPU",
                    reason="Rà soát tỷ lệ lỗi.",
                )
            ],
        )
        report = {
            "defect_rate": 2.5,
            "component_performance": [
                {
                    "component": "GPU",
                    "defect_rate": 1.2,
                }
            ],
        }

        with (
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DATA_QUALITY_MIN_SCORE",
                None,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DEFECT_RATE_TARGET",
                2.0,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DOWNTIME_TARGET_MINUTES",
                None,
            ),
        ):
            normalized = _normalize_recommendation_priorities(
                analysis,
                report,
            )

        self.assertEqual(
            normalized.recommended_actions[0].priority,
            "medium",
        )

    def test_recommendation_priority_defaults_to_medium_without_targets(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            recommended_actions=[
                RecommendedAction(
                    priority="critical",
                    action="Làm sạch dữ liệu.",
                    target_type="data",
                    target_name="input",
                    reason="Rà soát dữ liệu.",
                )
            ],
        )
        report = {
            "data_quality": {"quality_score": 20},
            "defect_rate": 15,
            "total_downtime": 999,
        }

        with (
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DATA_QUALITY_MIN_SCORE",
                None,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DEFECT_RATE_TARGET",
                None,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DOWNTIME_TARGET_MINUTES",
                None,
            ),
        ):
            normalized = _normalize_recommendation_priorities(
                analysis,
                report,
            )

        self.assertEqual(
            normalized.recommended_actions[0].priority,
            "medium",
        )

    def test_risk_levels_use_configured_data_quality_and_entity_thresholds(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            risk_areas=[
                RiskArea(
                    entity_type="data_quality",
                    entity_name="data_quality",
                    risk_level="medium",
                    evidence_keys=["quality_score"],
                    observation="Điểm chất lượng dữ liệu thấp.",
                ),
                RiskArea(
                    entity_type="component",
                    entity_name="GPU",
                    risk_level="critical",
                    evidence_keys=["production"],
                    observation="Tỷ lệ lỗi cần kiểm tra.",
                ),
                RiskArea(
                    entity_type="component",
                    entity_name="RAM",
                    risk_level="high",
                    evidence_keys=["defect_rate"],
                    observation="Tỷ lệ lỗi cần theo dõi.",
                ),
            ],
        )
        report = {
            "data_quality": {"quality_score": 57.9},
            "component_performance": [
                {
                    "component": "GPU",
                    "production": 100,
                    "defect_rate": 2.05,
                },
                {"component": "RAM", "defect_rate": 1.2},
            ],
        }

        with (
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DATA_QUALITY_MIN_SCORE",
                80,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DEFECT_RATE_TARGET",
                2.0,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DOWNTIME_TARGET_MINUTES",
                None,
            ),
        ):
            normalized = _normalize_risk_levels(analysis, report)

        self.assertEqual(
            [risk.risk_level for risk in normalized.risk_areas],
            ["high", "high", "medium"],
        )
        self.assertEqual(
            normalized.risk_areas[1].evidence_keys,
            ["production", "defect_rate"],
        )
        self.assertEqual(
            _get_entities(report, "data_quality"),
            {"data_quality"},
        )
        self.assertEqual(
            _find_entity_row(
                report,
                "data_quality",
                "data_quality",
            )["quality_score"],
            57.9,
        )

    def test_risk_levels_default_to_medium_without_thresholds(self):
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            risk_areas=[
                RiskArea(
                    entity_type="data_quality",
                    entity_name="data_quality",
                    risk_level="critical",
                    evidence_keys=["quality_score"],
                    observation="Dữ liệu cần rà soát.",
                )
            ],
        )
        report = {"data_quality": {"quality_score": 20}}

        with (
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DATA_QUALITY_MIN_SCORE",
                None,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DEFECT_RATE_TARGET",
                None,
            ),
            patch.object(
                hardware_ai_service_settings,
                "HARDWARE_DOWNTIME_TARGET_MINUTES",
                None,
            ),
        ):
            normalized = _normalize_risk_levels(analysis, report)

        self.assertEqual(
            normalized.risk_areas[0].risk_level,
            "medium",
        )

    def test_kpis_and_context_handle_missing_columns_and_values(self):
        dataframe = pd.DataFrame(
            {
                "Product": ["Widget A", "Widget B", "Widget C"],
                "Production": [100, 50, 40],
                "Defects": [5, None, "invalid"],
                "Downtime": [2, 1, 3],
                "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            }
        )

        kpis = hardware_service.calculate_hardware_kpis(dataframe)
        context = _build_ai_context(kpis)

        self.assertEqual(kpis["record_count"], 1)
        self.assertEqual(kpis["data_processing"]["rows_used_for_kpi"], 1)
        self.assertEqual(
            kpis["data_processing"]["invalid_metric_rows_excluded"],
            2,
        )
        self.assertEqual(kpis["component_performance"], [])
        self.assertEqual(kpis["defect_type_analysis"], [])
        self.assertIn("data_quality", context)
        self.assertIn("data_processing", context)
        self.assertEqual(context["metrics"]["total_production"], 100.0)
        self.assertEqual(context["product_performance"], [
            {
                "product": "Widget A",
                "production": 100.0,
                "defects": 5.0,
                "defect_rate": 5.0,
                "downtime": 2.0,
            }
        ])
        self.assertIsNone(context["highest_defect_component"])

    def test_context_has_stable_shape_when_optional_analysis_is_absent(self):
        context = _build_ai_context({})

        self.assertIsNone(context["metrics"]["total_defects"])
        self.assertEqual(context["supplier_performance"], [])
        self.assertEqual(context["data_quality"], {})
        self.assertEqual(context["data_processing"], {})
        self.assertIsNone(context["highest_defect_supplier"])

    def test_entities_and_rows_are_scoped_to_entity_type(self):
        report = {
            "product_performance": [
                {"product": " Shared ", "production": 10}
            ],
            "component_performance": [
                {"component": "Shared", "production": 20}
            ],
            "defect_type_analysis": [
                {"defect_type": "Shared", "defects": 2}
            ],
        }

        self.assertEqual(_get_entities(report, "product"), {"Shared"})
        self.assertEqual(_get_entities(report, "component"), {"Shared"})
        self.assertEqual(_get_entities(report, "supplier"), set())
        self.assertEqual(
            _find_entity_row(report, "product", "Shared")["production"],
            10,
        )
        self.assertEqual(
            _find_entity_row(report, "component", "Shared")["production"],
            20,
        )
        self.assertIsNone(_find_entity_row(report, "supplier", "Shared"))

    def test_each_entity_type_uses_its_own_analysis_group(self):
        report = {
            "product_performance": [{"product": "P"}],
            "model_performance": [{"model": "M"}],
            "component_performance": [{"component": "C"}],
            "supplier_performance": [{"supplier": "S"}],
            "batch_performance": [{"batch": "B"}],
            "defect_type_analysis": [{"defect_type": "D"}],
            "daily_production": [{"date": "2026-01-01"}],
        }
        expected_entities = {
            "product": "P",
            "model": "M",
            "component": "C",
            "supplier": "S",
            "batch": "B",
            "defect_type": "D",
            "date": "2026-01-01",
        }

        for entity_type, entity_name in expected_entities.items():
            with self.subTest(entity_type=entity_type):
                self.assertEqual(
                    _get_entities(report, entity_type),
                    {entity_name},
                )
                self.assertIsNotNone(
                    _find_entity_row(report, entity_type, entity_name)
                )

    def test_risk_evidence_is_limited_to_available_metrics(self):
        report = {
            "product_performance": [
                {
                    "product": "Widget A",
                    "production": 100,
                    "defects": 5,
                    "defect_rate": 5.0,
                    "downtime": None,
                }
            ],
        }
        risk = RiskArea(
            entity_type="product",
            entity_name="Widget A",
            risk_level="medium",
            evidence_keys=["production", "defects", "downtime"],
            observation="Có số liệu KPI cần xem xét.",
        )
        risk.evidence_keys.append("quality_score")
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            risk_areas=[risk],
        )

        validated = _validate_risk_areas(analysis, report)
        serialized_analysis = validated.model_dump()
        serialized_analysis["risk_areas"][0]["evidence"] = {
            "quality_score": 99,
            "unsupported_metric": 20,
        }
        result = enrich_risk_areas(serialized_analysis, report)

        self.assertEqual(
            result["risk_areas"][0]["evidence_keys"],
            ["production", "defects"],
        )
        self.assertEqual(
            result["risk_areas"][0]["evidence"],
            {"production": 100, "defects": 5},
        )

    def test_evidence_requires_numeric_metrics_and_uses_data_quality_row(self):
        report = {
            "product_performance": [
                {
                    "product": "Widget A",
                    "production": "100",
                    "defects": True,
                    "defect_rate": float("nan"),
                    "downtime": 2,
                }
            ],
            "data_quality": {"quality_score": 57.9},
        }
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            risk_areas=[
                RiskArea(
                    entity_type="product",
                    entity_name="Widget A",
                    risk_level="high",
                    evidence_keys=[
                        "production",
                        "defects",
                        "defect_rate",
                        "downtime",
                    ],
                    observation="Có số liệu cần xác minh.",
                ),
                RiskArea(
                    entity_type="data_quality",
                    entity_name="data_quality",
                    risk_level="high",
                    evidence_keys=["quality_score"],
                    observation="Điểm chất lượng cần xem xét.",
                ),
            ],
        )

        validated = _validate_risk_areas(analysis, report)
        output = validated.model_dump()
        output["risk_areas"][0]["evidence"] = {
            "production": 999999,
            "downtime": 999999,
        }
        result = enrich_risk_areas(output, report)

        self.assertEqual(
            result["risk_areas"][0]["evidence_keys"],
            ["downtime"],
        )
        self.assertEqual(
            result["risk_areas"][0]["evidence"],
            {"downtime": 2},
        )
        self.assertEqual(
            result["risk_areas"][1]["evidence"],
            {"quality_score": 57.9},
        )

    def test_risks_for_nonexistent_entities_are_dropped(self):
        report = {
            "product_performance": [
                {"product": "Widget A", "production": 100}
            ],
        }
        analysis = DeepAIAnalysis(
            executive_summary={"summary": "Tóm tắt."},
            production_analysis={"summary": "Sản xuất."},
            quality_analysis={"summary": "Chất lượng."},
            component_analysis={"summary": "Linh kiện."},
            supplier_analysis={"summary": "Nhà cung cấp."},
            batch_analysis={"summary": "Lô sản xuất."},
            downtime_analysis={"summary": "Thời gian dừng."},
            risk_areas=[
                RiskArea(
                    entity_type="component",
                    entity_name="Widget A",
                    risk_level="high",
                    evidence_keys=["production"],
                    observation="Không khớp loại entity.",
                )
            ],
        )

        validated = _validate_risk_areas(analysis, report)

        self.assertEqual(validated.risk_areas, [])

    def test_deep_analysis_receives_raw_kpi_dictionary(self):
        raw_kpis = {
            "total_production": 100,
            "product_performance": [],
        }
        hardware_insights = Mock()
        hardware_insights.model_dump.return_value = {
            "kpis": [{"name": "wrong-shaped-source"}]
        }

        with (
            patch.object(
                hardware_service,
                "calculate_hardware_kpis",
                return_value=raw_kpis,
            ),
            patch.object(
                hardware_service,
                "generate_ai_insights",
                return_value=hardware_insights,
            ),
            patch.object(
                hardware_service,
                "analyze_hardware_deep",
                return_value={"risk_areas": []},
            ) as deep_analysis,
        ):
            result = hardware_service.analyze_hardware_dataframe(
                pd.DataFrame({"Production": [100]})
            )

        deep_analysis.assert_called_once_with(raw_kpis)
        self.assertEqual(result["ai_analysis"], {"risk_areas": []})


if __name__ == "__main__":
    unittest.main()
