import unittest

import pandas as pd

from app.services.hardware_ai_service import _build_ai_context
from app.services.hardware_standards import (
    detect_hardware_standards,
    get_hardware_standards_pack,
)
from app.services.analysis_readiness_service import classify_analysis_dataset
from app.services.hardware_knowledge_base import (
    normalize_unit_label,
    profile_hardware_dataframe,
)
from app.services.manufacturing_data_foundation import (
    normalize_manufacturing_dataframe,
)


class HardwareStandardsTests(unittest.TestCase):
    def test_p0_pack_contains_all_requested_standards(self):
        pack = get_hardware_standards_pack()

        self.assertEqual(pack["pack_id"], "hardware-standards-p0")
        self.assertEqual(pack["priority"], "P0")
        ids = {item["id"] for item in pack["standards"]}
        self.assertEqual(
            ids,
            {
                "SMBIOS",
                "DMI",
                "UEFI",
                "ACPI",
                "CIM",
                "REDFISH",
                "IPMI",
                "UEFI_PI",
            },
        )
        self.assertTrue(
            all(item["source"]["url"].startswith("https://") for item in pack["standards"])
        )

    def test_detects_smbios_cim_and_redfish_from_file_headers(self):
        dataframe = pd.DataFrame(
            {
                "System Manufacturer": ["Dell"],
                "BIOS Vendor": ["Dell Inc."],
                "Processor": ["Xeon"],
                "Memory Device": ["DDR5"],
                "Fan Speed": ["4200 RPM"],
                "Temperature Sensor": ["CPU"],
                "BMC Status": ["Healthy"],
            }
        )

        result = detect_hardware_standards(dataframe)
        ids = set(result["detected_ids"])

        self.assertIn("SMBIOS", ids)
        self.assertIn("CIM", ids)
        self.assertIn("REDFISH", ids)
        self.assertIn("IPMI", ids)

    def test_ai_context_contains_hardware_standards_metadata(self):
        standards = detect_hardware_standards(
            text="UEFI firmware version and Secure Boot configuration"
        )
        context = _build_ai_context(
            {
                "hardware_standards": standards,
                "total_production": 100,
                "record_count": 1,
            }
        )

        self.assertEqual(
            context["hardware_standards"]["detected_ids"],
            ["UEFI"],
        )

    def test_text_detection_does_not_require_dataframe(self):
        result = detect_hardware_standards(
            text="ACPI thermal zone power state battery"
        )

        self.assertEqual(result["detected_ids"], ["ACPI"])
        self.assertIn("thermal", result["physical_hardware_focus"])

    def test_hardware_profile_maps_known_and_reports_unknown_columns(self):
        dataframe = pd.DataFrame(
            {
                "System Manufacturer": ["Acme"],
                "CPU Temperature": [72],
                "Vendor Diagnostic Flag": ["ok"],
            }
        )

        profile = profile_hardware_dataframe(dataframe)

        self.assertEqual(
            profile["column_mapping"]["System Manufacturer"],
            "attributes.system_manufacturer",
        )
        self.assertEqual(
            profile["unknown_fields"],
            [{
                "source_column": "Vendor Diagnostic Flag",
                "inferred_category": None,
                "inferred_type": "string",
                "inferred_meaning": (
                    "Meaning cannot be inferred reliably from available data."
                ),
            }],
        )
        self.assertEqual(profile["raw_columns"], list(dataframe.columns))
        self.assertTrue(profile["raw_values_preserved"])

    def test_normalization_keeps_raw_values_for_known_and_unknown_columns(self):
        dataframe = pd.DataFrame(
            {
                "System Manufacturer": ["Acme"],
                "CPU Temperature": [72],
                "Uncatalogued Diagnostic": ["vendor-value"],
            }
        )

        normalized = normalize_manufacturing_dataframe(
            dataframe,
            require_production=False,
        )
        attributes = normalized.records[0].attributes

        self.assertEqual(attributes["system_manufacturer"], "Acme")
        self.assertEqual(attributes["cpu_temperature"], 72)
        self.assertEqual(
            attributes["Uncatalogued_Diagnostic"],
            "vendor-value",
        )
        self.assertEqual(dataframe["CPU Temperature"].iloc[0], 72)

    def test_unknown_hardware_field_is_classified_without_discarding_it(self):
        dataframe = pd.DataFrame(
            {
                "Chassis Fan Speed": [1800],
                "Uncatalogued Vendor Metric": ["raw"],
            }
        )

        classification = classify_analysis_dataset(dataframe)
        profile = classification["hardware_profile"]

        self.assertTrue(classification["analyses"]["general"]["ready"])
        self.assertIn("hardware", classification["domains"])
        unknown_by_column = {
            field["source_column"]: field["inferred_category"]
            for field in profile["unknown_fields"]
        }
        self.assertEqual(
            unknown_by_column["Uncatalogued Vendor Metric"],
            None,
        )
        self.assertIn(
            {"source_column": "Chassis Fan Speed", "sensor_id": "fan_speed", "unit": "rpm"},
            profile["recognized_sensors"],
        )

    def test_unit_alias_normalization(self):
        self.assertEqual(normalize_unit_label("°C"), "Cel")
        self.assertEqual(normalize_unit_label("GiB"), "GiBy")
        self.assertEqual(normalize_unit_label("made-up-unit"), "made-up-unit")

