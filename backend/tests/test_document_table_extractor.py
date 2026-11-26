import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image
from docx import Document
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.config import settings
from app.routes.hardware import _parse_hardware_content
from app.services.document_table_extractor import (
    extract_document_tables_with_gemini,
)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (4, 4), color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _office_archive(image: bytes | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if image is not None:
            archive.writestr("xl/media/image1.png", image)
    return output.getvalue()


def _gemini_response_client(tables: list[dict[str, object]]) -> SimpleNamespace:
    response = SimpleNamespace(
        text=json.dumps({"tables": tables}, ensure_ascii=False)
    )
    generate_content = Mock(return_value=response)
    return SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )


class DocumentTableExtractorTests(unittest.TestCase):
    def test_extracts_table_from_embedded_office_image_with_project_key(self):
        client = _gemini_response_client(
            [
                {
                    "headers": ["Production", "Defects"],
                    "rows": [["120", "3"]],
                }
            ]
        )
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch(
                "app.services.document_table_extractor.get_client",
                return_value=client,
            ),
            patch("app.services.document_table_extractor.AIAuditContext"),
        ):
            dataframe = extract_document_tables_with_gemini(
                "workbook.xlsx",
                _office_archive(_png_bytes()),
                None,
            )

        self.assertIsNotNone(dataframe)
        self.assertEqual(dataframe.to_dict(orient="records"), [
            {"Production": "120", "Defects": "3"},
        ])
        client.models.generate_content.assert_called_once()

    def test_extracts_table_from_embedded_docx_image(self):
        document = Document()
        document.add_paragraph("Production records")
        document.add_picture(io.BytesIO(_png_bytes()))
        docx_buffer = io.BytesIO()
        document.save(docx_buffer)
        client = _gemini_response_client(
            [
                {
                    "headers": ["Production", "Defects"],
                    "rows": [["90", "1"]],
                }
            ]
        )
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch(
                "app.services.document_table_extractor.get_client",
                return_value=client,
            ),
            patch("app.services.document_table_extractor.AIAuditContext"),
        ):
            dataframe = extract_document_tables_with_gemini(
                "report.docx",
                docx_buffer.getvalue(),
                None,
            )

        self.assertIsNotNone(dataframe)
        self.assertEqual(dataframe.iloc[0]["Production"], "90")
        client.models.generate_content.assert_called_once()

    def test_hardware_upload_merges_vision_rows_from_embedded_xlsx_image(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["Production", "Defects"])
        worksheet.append([100, 2])
        image_buffer = io.BytesIO(_png_bytes())
        worksheet.add_image(ExcelImage(image_buffer), "D1")
        workbook_buffer = io.BytesIO()
        workbook.save(workbook_buffer)

        client = _gemini_response_client(
            [
                {
                    "headers": ["Production", "Defects"],
                    "rows": [["120", "3"]],
                }
            ]
        )
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch(
                "app.services.document_table_extractor.get_client",
                return_value=client,
            ),
            patch("app.services.document_table_extractor.AIAuditContext"),
        ):
            dataframe = _parse_hardware_content(
                "workbook.xlsx",
                workbook_buffer.getvalue(),
                allow_documents=True,
            )

        self.assertEqual(len(dataframe.index), 2)
        self.assertEqual(dataframe.iloc[0]["Production"], 100)
        self.assertEqual(dataframe.iloc[1]["Production"], "120")

    def test_extracts_tables_from_image_based_pdf(self):
        pdf_buffer = io.BytesIO()
        pdf = canvas.Canvas(pdf_buffer)
        pdf.drawImage(
            ImageReader(io.BytesIO(_png_bytes())),
            40,
            100,
            width=100,
            height=100,
        )
        pdf.save()
        client = _gemini_response_client(
            [
                {
                    "headers": ["Date", "Production"],
                    "rows": [["2026-01-01", "100"]],
                }
            ]
        )
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-key"),
            patch(
                "app.services.document_table_extractor.get_client",
                return_value=client,
            ),
            patch("app.services.document_table_extractor.AIAuditContext"),
        ):
            dataframe = extract_document_tables_with_gemini(
                "scanned.pdf",
                pdf_buffer.getvalue(),
                None,
            )

        self.assertIsNotNone(dataframe)
        self.assertEqual(dataframe.iloc[0]["Production"], "100")
        client.models.generate_content.assert_called_once()

    def test_does_not_call_gemini_for_office_file_without_images(self):
        with patch(
            "app.services.document_table_extractor.get_client"
        ) as get_client:
            result = extract_document_tables_with_gemini(
                "workbook.xlsx",
                _office_archive(),
                pd.DataFrame({"Production": [1]}),
            )

        self.assertIsNone(result)
        get_client.assert_not_called()

    def test_requires_project_api_key_when_image_extraction_is_needed(self):
        from fastapi import HTTPException

        with (
            patch.object(settings, "GEMINI_API_KEY", None),
            self.assertRaises(HTTPException) as error,
        ):
            extract_document_tables_with_gemini(
                "workbook.xlsx",
                _office_archive(_png_bytes()),
                None,
            )

        self.assertEqual(error.exception.status_code, 503)
