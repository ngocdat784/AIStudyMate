import unittest
from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook
from pypdf import PdfReader

from app.services.hardware_report_service import (
    build_hardware_pdf,
    build_hardware_xlsx,
)


SAMPLE_ANALYSIS = {
    "title": "Báo cáo phân tích phần cứng",
    "summary": "Tóm tắt chất lượng sản xuất.",
    "data_scope": {"has_model_data": True},
    "kpis": [
        {
            "name": "Tổng sản lượng",
            "value": 1200,
            "unit": "sản phẩm",
            "description": "Tổng sản lượng đã ghi nhận.",
        },
        {
            "name": "Tỷ lệ lỗi chung",
            "value": 2.5,
            "unit": "%",
            "description": "Tỷ lệ lỗi.",
        },
    ],
    "daily_production": [
        {
            "date": "2026-10-01",
            "production": 1200,
            "defects": 30,
            "defect_rate": 2.5,
            "downtime": 5,
        }
    ],
    "product_performance": [{"product": "Widget", "production": 1200}],
    "model_performance": [{"model": "Model A", "production": 1200}],
    "component_performance": [{"component": "Board", "production": 1200}],
    "supplier_performance": [{"supplier": "Supplier X", "defects": 30}],
    "batch_performance": [{"batch": "Batch 1", "defects": 30}],
    "defect_type_analysis": [{"defect_type": "Scratch", "defects": 30}],
    "data_quality": {"quality_status": "good", "quality_score": 95},
    "data_processing": {"rows_used_for_kpi": 1},
    "insights": ["Theo dõi tỷ lệ lỗi."],
    "warnings": ["Một số trường còn thiếu."],
    "recommendations": ["Kiểm tra lại dữ liệu đầu vào."],
    "ai_analysis": {
        "executive_summary": {
            "summary": "Sản xuất ổn định.",
            "key_observations": ["Có dữ liệu đủ dùng."],
        },
        "risk_areas": [],
        "recommended_actions": [],
    },
}


class HardwareReportServiceTests(unittest.TestCase):
    def test_xlsx_contains_all_report_sheets_and_analysis_data(self):
        workbook_bytes = build_hardware_xlsx(SAMPLE_ANALYSIS)
        workbook = load_workbook(BytesIO(workbook_bytes), data_only=True)

        self.assertEqual(
            workbook.sheetnames,
            [
                "Executive Summary",
                "KPI Overview",
                "Production Analysis",
                "Quality Analysis",
                "Supplier Analysis",
                "Batch Analysis",
                "Defect Analysis",
                "Manufacturing Analytics",
                "Machine Analysis",
                "Material Analysis",
                "Predictive Analysis",
                "Dataset Classification",
                "AI Insights",
                "Warnings",
                "Recommendations",
            ],
        )
        kpi_values = [
            cell.value
            for row in workbook["KPI Overview"].iter_rows()
            for cell in row
        ]
        self.assertIn("Tổng sản lượng", kpi_values)
        self.assertIn(1200, kpi_values)
        self.assertIn("No data provided by analysis", [
            cell.value
            for row in workbook["AI Insights"].iter_rows()
            for cell in row
        ])

    def test_xlsx_includes_only_selected_report_sections(self):
        workbook = load_workbook(
            BytesIO(
                build_hardware_xlsx(
                    {
                        **SAMPLE_ANALYSIS,
                        "export_sections": [
                            "kpi_overview",
                            "batch_analysis",
                        ],
                    }
                )
            ),
            data_only=True,
        )

        self.assertEqual(
            workbook.sheetnames,
            ["KPI Overview", "Batch Analysis"],
        )
        batch_values = [
            cell.value
            for row in workbook["Batch Analysis"].iter_rows()
            for cell in row
        ]
        self.assertIn("Batch 1", batch_values)

    def test_xlsx_includes_organization_factory_and_plant_scope(self):
        workbook = load_workbook(
            BytesIO(
                build_hardware_xlsx(
                    {
                        **SAMPLE_ANALYSIS,
                        "report_scope": {
                            "organization": "Organization A",
                            "factory": "Factory A",
                            "plant": "Plant A",
                        },
                    },
                    ["executive_summary"],
                )
            ),
            data_only=True,
        )

        worksheet = workbook["Executive Summary"]
        self.assertEqual(
            [worksheet.cell(row=2, column=column).value for column in range(1, 7)],
            [
                "Organization",
                "Organization A",
                "Factory",
                "Factory A",
                "Plant",
                "Plant A",
            ],
        )

    def test_xlsx_escapes_formula_like_user_values(self):
        workbook = load_workbook(
            BytesIO(
                build_hardware_xlsx(
                    {
                        "title": "Report",
                        "insights": ["=HYPERLINK(\"https://example.com\")"],
                    }
                )
            ),
            data_only=True,
        )
        insight_values = [
            cell.value
            for row in workbook["AI Insights"].iter_rows()
            for cell in row
        ]

        self.assertIn(
            "'=HYPERLINK(\"https://example.com\")",
            insight_values,
        )

    def test_pdf_has_pdf_signature_and_vietnamese_report_text(self):
        pdf_bytes = build_hardware_pdf(SAMPLE_ANALYSIS)
        extracted_text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(BytesIO(pdf_bytes)).pages
        )

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertIn("HardwareAI Report", extracted_text)
        self.assertIn("Tóm tắt chất lượng sản xuất.", extracted_text)
        self.assertIn("Recommendations", extracted_text)

    def test_pdf_includes_only_selected_report_sections(self):
        pdf_bytes = build_hardware_pdf(
            SAMPLE_ANALYSIS,
            ["kpi_overview"],
        )
        extracted_text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(BytesIO(pdf_bytes)).pages
        )

        self.assertIn("KPI Overview", extracted_text)
        self.assertNotIn("Production Analysis", extracted_text)
        self.assertNotIn("Recommendations", extracted_text)

    def test_pdf_includes_organization_factory_and_plant_scope(self):
        pdf_bytes = build_hardware_pdf(
            {
                **SAMPLE_ANALYSIS,
                "report_scope": {
                    "organization": "Organization A",
                    "factory": "Factory A",
                    "plant": "Plant A",
                },
            },
            ["executive_summary"],
        )
        extracted_text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(BytesIO(pdf_bytes)).pages
        )

        self.assertIn("Organization A", extracted_text)
        self.assertIn("Factory A", extracted_text)
        self.assertIn("Plant A", extracted_text)


if __name__ == "__main__":
    unittest.main()
