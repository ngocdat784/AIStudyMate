import csv
import io
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.routes.hardware import (
    MAX_FILE_SIZE,
    MAX_MULTIPLE_FILES,
    _read_csv_robust,
)


class HardwareMultipleUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_openapi_marks_each_upload_as_a_file(self):
        schema = app.openapi()
        request_body = schema["paths"][
            "/api/hardware/analyze-multiple"
        ]["post"]["requestBody"]
        schema_reference = request_body["content"]["multipart/form-data"][
            "schema"
        ]["$ref"]
        schema_name = schema_reference.rsplit("/", 1)[-1]
        files_schema = schema["components"]["schemas"][schema_name][
            "properties"
        ]["files"]

        self.assertEqual(files_schema["type"], "array")
        self.assertEqual(
            files_schema["items"],
            {"type": "string", "format": "binary"},
        )

    def test_semicolon_parser_falls_back_when_sniffer_cannot_detect(self):
        content = (
            b"Date;Product;Production\n"
            b"2026-10-01;Laptop;100\n"
            b"2026-10-02;Desktop;200\n"
        )

        with patch(
            "app.routes.hardware.csv.Sniffer.sniff",
            side_effect=csv.Error("could not determine delimiter"),
        ):
            dataframe = _read_csv_robust(content)

        self.assertEqual(
            dataframe.columns.tolist(),
            ["Date", "Product", "Production"],
        )
        self.assertEqual(dataframe.shape, (2, 3))
        self.assertEqual(dataframe.iloc[1]["Product"], "Desktop")

    def test_parser_falls_back_for_unbalanced_quotes(self):
        content = (
            b"Date;Product;Production\n"
            b'2026-10-01;"Laptop;100\n'
            b"2026-10-02;Desktop;200\n"
        )

        dataframe = _read_csv_robust(content)

        self.assertEqual(
            dataframe.columns.tolist(),
            ["Date", "Product", "Production"],
        )
        self.assertEqual(dataframe.shape, (2, 3))
        self.assertEqual(dataframe.iloc[0]["Product"], '"Laptop')
        self.assertEqual(dataframe.iloc[1]["Production"], "200")

    def test_parser_removes_outer_quotes_around_each_csv_line(self):
        content = (
            '"Date;Product;Model;Production;Defects"\n'
            '"2026-10-01;Laptop;ProBook X1;100;2"\n'
            '"2026-10-02;Desktop;WorkStation;200;3"\n'
        ).encode("utf-8")

        dataframe = _read_csv_robust(content)

        self.assertEqual(
            dataframe.columns.tolist(),
            ["Date", "Product", "Model", "Production", "Defects"],
        )
        self.assertEqual(dataframe.shape, (2, 5))
        self.assertEqual(dataframe.iloc[0]["Model"], "ProBook X1")
        self.assertEqual(dataframe.iloc[1]["Production"], "200")

    def test_endpoint_prepares_csv_and_xlsx_separately_with_source_names(self):
        excel_buffer = io.BytesIO()
        pd.DataFrame(
            {
                "Model": ["B"],
                "Production": [20],
                "Defects": [2],
            }
        ).to_excel(excel_buffer, index=False)

        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", ("first.csv", b"Model,Production\nA,10\n", "text/csv")),
                (
                    "files",
                    (
                        "second.xlsx",
                        excel_buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["file_count"], 2)
        self.assertEqual(payload["total_rows"], 2)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["files_merged"], 2)
        self.assertEqual(payload["total_rows_before_merge"], 2)
        self.assertEqual(payload["total_rows_after_merge"], 2)
        self.assertEqual(payload["duplicate_rows_detected"], 0)
        self.assertEqual(
            [item["filename"] for item in payload["files"]],
            ["first.csv", "second.xlsx"],
        )
        self.assertEqual(
            payload["files"][0]["preview"][0]["_source_file"],
            "first.csv",
        )
        self.assertEqual(
            payload["files"][1]["preview"][0]["_source_file"],
            "second.xlsx",
        )
        self.assertEqual(payload["files"][0]["row_count"], 1)
        self.assertEqual(payload["files"][1]["row_count"], 1)
        self.assertEqual(
            payload["merged_rows"][0]["source_file"],
            "first.csv",
        )
        self.assertEqual(
            payload["schema_columns"][:11],
            [
                "Date",
                "Product",
                "Model",
                "Component",
                "Supplier",
                "Batch",
                "Production",
                "Defects",
                "Downtime",
                "Defect Type",
                "Quality Status",
            ],
        )
        self.assertEqual(
            payload["schema_columns"][-1],
            "source_file",
        )
        self.assertIsNone(payload["merged_rows"][0]["Date"])

    def test_semicolon_csv_is_parsed_and_defect_type_alias_is_normalized(self):
        semicolon_csv = (
            "Date;Product;Model;Component;Supplier;Batch;"
            "Defect_Type;Production;Defects;Downtime\n"
            "2026-10-01;Laptop;Model A;Screen;Supplier X;Batch 1;"
            "Dead pixel;100;2;5\n"
        ).encode("utf-8")

        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    ("hardware_quality_test.csv", semicolon_csv, "text/csv"),
                ),
                (
                    "files",
                    (
                        "production_test.csv",
                        b"Date,Model,Production\n2026-10-02,Model B,200\n",
                        "text/csv",
                    ),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["files_merged"], 2)
        self.assertEqual(payload["total_rows_after_merge"], 2)
        quality_file = payload["files"][0]
        self.assertIn("Production", quality_file["columns"])
        self.assertIn("Defect Type", quality_file["columns"])
        self.assertNotIn("Defect_Type", quality_file["columns"])
        self.assertEqual(
            payload["merged_rows"][0]["Defect Type"],
            "Dead pixel",
        )
        self.assertEqual(
            payload["merged_rows"][0]["source_file"],
            "hardware_quality_test.csv",
        )

    def test_semicolon_and_comma_files_merge_all_42_rows(self):
        quality_rows = [
            (
                f"2026-10-{day:02d};Laptop;Model A;Screen;Supplier X;"
                f"Batch {day};Dead pixel;100;2;5"
            )
            for day in range(1, 20)
        ]
        production_rows = [
            (
                f"2026-11-{day:02d},Laptop,Model B,Battery,"
                f"Supplier Y,Batch {day},,200,3,4"
            )
            for day in range(1, 24)
        ]
        quality_csv = (
            "Date;Product;Model;Component;Supplier;Batch;"
            "Defect_Type;Production;Defects;Downtime\n"
            + "\n".join(quality_rows)
            + "\n"
        ).encode("utf-8")
        production_csv = (
            "Date,Product,Model,Component,Supplier,Batch,"
            "Defect Type,Production,Defects,Downtime\n"
            + "\n".join(production_rows)
            + "\n"
        ).encode("utf-8")

        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", ("hardware_quality_test.csv", quality_csv, "text/csv")),
                ("files", ("production_test.csv", production_csv, "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["files_processed"], 2)
        self.assertEqual(payload["files_valid"], 2)
        self.assertEqual(payload["files_merged"], 2)
        self.assertEqual(payload["files_skipped"], 0)
        self.assertEqual(payload["total_rows_after_merge"], 42)
        self.assertEqual(payload["rows_skipped"], 0)
        self.assertEqual(
            [report["rows_merged"] for report in payload["file_reports"]],
            [19, 23],
        )
        self.assertEqual(
            payload["files"][0]["columns"][:10],
            [
                "Date",
                "Product",
                "Model",
                "Component",
                "Supplier",
                "Batch",
                "Defect Type",
                "Production",
                "Defects",
                "Downtime",
            ],
        )

    def test_endpoint_normalizes_known_aliases_and_reports_invalid_values(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "January.csv",
                        (
                            b"DATE,Product Name,Model,Production,Defects\n"
                            b"2026-01-15,Widget,M1,\"1,200\",5\n"
                        ),
                        "text/csv",
                    ),
                ),
                (
                    "files",
                    (
                        "March.csv",
                        (
                            b"Date,Product Name,Model,Production,Defects\n"
                            b"01/02/2026,Widget,M1,\"1,2\",-1\n"
                        ),
                        "text/csv",
                    ),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed_with_warnings")
        self.assertEqual(payload["files_valid"], 1)
        self.assertEqual(payload["files_with_warnings"], 1)

        january, march = payload["file_reports"]
        self.assertEqual(january["status"], "merged")
        self.assertEqual(january["validation_status"], "valid")
        self.assertEqual(january["column_mapping"]["DATE"], "Date")
        self.assertEqual(
            january["column_mapping"]["Product Name"],
            "Product",
        )
        self.assertEqual(
            january["missing_values"]["Component"],
            1,
        )
        self.assertNotIn("Component", january["missing_required_columns"])
        self.assertEqual(march["status"], "merged_with_warnings")
        self.assertEqual(march["validation_status"], "warning")
        self.assertEqual(march["invalid_dates"], 1)
        self.assertEqual(march["invalid_numeric_values"], 1)
        self.assertEqual(march["negative_values"], 1)
        self.assertEqual(
            payload["files"][0]["preview"][0]["Production"],
            1200.0,
        )
        self.assertEqual(
            payload["files"][1]["preview"][0]["Date"],
            "01/02/2026",
        )
        self.assertEqual(payload["files_merged"], 2)
        self.assertEqual(payload["duplicate_rows_detected"], 0)
        self.assertEqual(
            payload["merged_rows"][0]["Production"],
            1200.0,
        )
        self.assertEqual(
            payload["merged_rows"][1]["Production"],
            "1,2",
        )
        self.assertEqual(payload["merged_rows"][1]["Defects"], -1)

    def test_existing_source_column_is_preserved(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "first.csv",
                        (
                            b"Model,Production,_source_file,source_file\n"
                            b"A,100,original-source-column,original-value\n"
                        ),
                        "text/csv",
                    ),
                ),
                ("files", ("second.csv",                 b"Model,Production\nB,50\n", "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["files"][0]
        self.assertEqual(item["source_column"], "_source_file_")
        self.assertEqual(
            item["preview"][0]["source_file"],
            "original-value",
        )
        self.assertEqual(
            item["preview"][0]["_source_file"],
            "original-source-column",
        )
        self.assertEqual(item["preview"][0]["_source_file_"], "first.csv")
        self.assertEqual(
            response.json()["merged_rows"][0]["source_file"],
            "first.csv",
        )
        self.assertEqual(
            response.json()["merged_rows"][0]["source_file_original"],
            "original-value",
        )

    def test_endpoint_requires_at_least_two_files(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", ("only.csv", b"Model\nA\n", "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ít nhất hai file", response.json()["detail"])

    def test_endpoint_rejects_more_than_maximum_file_count(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", (f"file-{index}.csv", b"Model\nA\n", "text/csv"))
                for index in range(MAX_MULTIPLE_FILES + 1)
            ],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            f"tối đa {MAX_MULTIPLE_FILES} file",
            response.json()["detail"],
        )

    def test_each_file_is_validated_independently(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    ("first.csv", b"Production\n10\n", "text/csv"),
                ),
                ("files", ("unsupported.json", b"{}", "application/json")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["files_failed"], 1)
        self.assertIn("unsupported.json", payload["file_reports"][1]["filename"])
        self.assertIn(
            "Chỉ hỗ trợ file CSV hoặc XLSX",
            payload["file_reports"][1]["error"],
        )

    def test_file_size_limit_is_applied_to_each_upload(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    ("first.csv", b"Production\n10\n", "text/csv"),
                ),
                (
                    "files",
                    ("too-large.csv", b"x" * (MAX_FILE_SIZE + 1), "text/csv"),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["files_failed"], 1)
        self.assertIn("too-large.csv", payload["file_reports"][1]["filename"])
        self.assertIn("10MB", payload["file_reports"][1]["error"])

    def test_missing_required_column_fails_only_that_file(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    ("valid.csv", b"Production\n100\n", "text/csv"),
                ),
                (
                    "files",
                    ("missing.csv", b"Date,Product\n2026-01-01,A\n", "text/csv"),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed_with_warnings")
        self.assertEqual(payload["files_valid"], 1)
        self.assertEqual(payload["files_failed"], 1)
        failed_report = payload["file_reports"][1]
        self.assertEqual(failed_report["status"], "skipped")
        self.assertEqual(failed_report["validation_status"], "failed")
        self.assertEqual(failed_report["missing_required_columns"], ["Production"])
        self.assertEqual(payload["files_merged"], 1)
        self.assertEqual(payload["files_skipped"], 1)
        self.assertEqual(payload["rows_skipped"], 1)
        self.assertEqual(payload["total_rows_after_merge"], 1)

    def test_defect_type_is_not_mapped_to_component(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "first.csv",
                        b"Production,Defect Type\n100,Scratch\n",
                        "text/csv",
                    ),
                ),
                ("files", ("second.csv", b"Production\n50\n", "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        report = response.json()["file_reports"][0]
        self.assertIn("Component", report["missing_optional_columns"])
        self.assertIn("Defect Type", response.json()["files"][0]["columns"])
        self.assertNotIn("Component", response.json()["files"][0]["columns"])

    def test_alias_collisions_fail_without_picking_a_column(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "conflict.csv",
                        b"Production,Output\n100,90\n",
                        "text/csv",
                    ),
                ),
                ("files", ("valid.csv", b"Production\n50\n", "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        report = response.json()["file_reports"][0]
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["validation_status"], "failed")
        self.assertIn("Production", report["error"])

    def test_exact_duplicates_are_reported_but_not_removed(self):
        row = b"Date,Product,Model,Production,Defects\n2026-10-01,Laptop,X1,1200,25\n"
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", ("January.csv", row, "text/csv")),
                ("files", ("February.csv", row, "text/csv")),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed_with_warnings")
        self.assertEqual(payload["duplicate_rows_detected"], 1)
        self.assertEqual(payload["total_rows_before_merge"], 2)
        self.assertEqual(payload["total_rows_after_merge"], 2)
        self.assertEqual(payload["rows_skipped"], 0)
        self.assertEqual(len(payload["merged_rows"]), 2)
        self.assertEqual(
            [row["source_file"] for row in payload["merged_rows"]],
            ["January.csv", "February.csv"],
        )
        self.assertEqual(payload["file_reports"][0]["status"], "merged")
        self.assertEqual(
            payload["file_reports"][1]["status"],
            "merged_with_warnings",
        )
        self.assertEqual(
            payload["file_reports"][1]["duplicate_rows_detected"],
            1,
        )

    def test_duplicate_detection_keeps_distinct_rows_for_same_product_model(self):
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                (
                    "files",
                    (
                        "shifts.csv",
                        (
                            b"Date,Product,Model,Production,Defects\n"
                            b"2026-10-01,Laptop,X1,1200,25\n"
                            b"2026-10-01,Laptop,X1,600,12\n"
                        ),
                        "text/csv",
                    ),
                ),
                (
                    "files",
                    ("other.csv", b"Production\n10\n", "text/csv"),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["duplicate_rows_detected"], 0)
        self.assertEqual(payload["total_rows_after_merge"], 3)

    def test_row_quality_counts_are_reported_per_metric_and_aggregated(self):
        mixed_quality_csv = (
            b"Date,Model,Production,Defects,Downtime\n"
            b"2026-10-01,A,100,2,5\n"
            b"invalid-date,B,invalid,3,\n"
            b",C,0,0,-1\n"
        )
        response = self.client.post(
            "/api/hardware/analyze-multiple",
            files=[
                ("files", ("mixed.csv", mixed_quality_csv, "text/csv")),
                (
                    "files",
                    ("production.csv", b"Model,Production\nD,50\n", "text/csv"),
                ),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        mixed_report, production_report = payload["file_reports"]

        self.assertEqual(payload["rows_merged"], 4)
        self.assertEqual(payload["rows_with_warnings"], 2)
        self.assertEqual(
            mixed_report["rows_valid_for_kpi"],
            {
                "production": 2,
                "defects": 3,
                "defect_rate": 2,
                "downtime": 1,
            },
        )
        self.assertEqual(
            mixed_report["rows_excluded_from_analysis"],
            {
                "production": 1,
                "defects": 0,
                "defect_rate": 1,
                "downtime": 2,
                "time_series": 2,
            },
        )
        self.assertEqual(
            production_report["rows_valid_for_kpi"],
            {
                "production": 1,
                "defects": 0,
                "defect_rate": 0,
                "downtime": 0,
            },
        )
        self.assertEqual(
            payload["rows_valid_for_kpi"],
            {
                "production": 3,
                "defects": 3,
                "defect_rate": 2,
                "downtime": 1,
            },
        )
        self.assertEqual(
            payload["rows_excluded_from_analysis"],
            {
                "production": 1,
                "defects": 1,
                "defect_rate": 2,
                "downtime": 3,
                "time_series": 3,
            },
        )


if __name__ == "__main__":
    unittest.main()
