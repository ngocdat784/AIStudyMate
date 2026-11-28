import io
import unittest

from openpyxl import Workbook, load_workbook

from app.services.hardware_workbook_analysis import analyze_hardware_workbook


def _phase_one_workbook() -> bytes:
    workbook = Workbook()
    readme = workbook.active
    readme.title = "README"
    readme.append(["HardwareAI test workbook"])
    readme.append(["Purpose", "Synthetic coverage for hardware standards"])

    sheets = {
        "SMBIOS_DMI": [
            ["Workbook title"],
            [],
            [
                "record_id", "smbios_structure_type", "structure_name", "handle",
                "manufacturer", "product_name", "physical_or_firmware",
            ],
            *[
                [f"S{kind}", kind, name, f"0x{kind:04X}", "Example", name, category]
                for kind, name, category in (
                    (0, "BIOS Information", "firmware_metadata"),
                    (1, "System Information", "physical_inventory"),
                    (2, "Baseboard Information", "physical_inventory"),
                    (3, "System Enclosure", "physical_inventory"),
                    (4, "Processor Information", "physical_inventory"),
                    (17, "Memory Device", "physical_inventory"),
                )
            ],
        ],
        "UEFI_Firmware": [
            ["variable_name", "vendor_guid", "value_raw", "attributes", "boot_related"],
            ["BootOrder", "guid", "0001", "NV|BS|RT", "yes"],
            ["SecureBoot", "guid", "1", "NV|BS|RT", "yes"],
            ["SetupMode", "guid", "0", "NV|BS|RT", "yes"],
        ],
        "ACPI_Devices": [
            ["acpi_object_path", "object_type", "hardware_id", "resource_type", "resource_value", "power_state", "notes"],
            [r"\_SB.PCI0.THM0", "ThermalZone", "N/A", "temperature", 42, "active", "Celsius"],
        ],
        "CIM_Inventory": [
            ["cim_namespace", "cim_class", "property_name", "property_value", "data_type", "unit"],
            ["root\\cimv2", "Win32_Processor", "NumberOfCores", 8, "uint32", "count"],
        ],
        "Redfish_Thermal": [
            ["resource_uri", "resource_type", "member_id", "sensor_name", "reading_type", "reading", "reading_units", "status_health", "status_state", "timestamp"],
            ["/redfish/v1/Chassis/1/Thermal", "Thermal", "CPU_Temp", "CPU Package", "Temperature", 61, "Celsius", "OK", "Enabled", "2026-11-28T08:00:00+07:00"],
            ["/redfish/v1/Chassis/1/Thermal", "Thermal", "CPU_Temp", "CPU Package", "Temperature", 62, "Celsius", "OK", "Enabled", "2026-11-28T08:01:00+07:00"],
        ],
        "IPMI_Sensors": [
            ["sensor_number", "sensor_name", "sensor_type", "reading_value", "unit", "lower_critical", "upper_critical", "event_status", "sensor_owner", "timestamp"],
            ["0x01", "CPU Temp", "Temperature", 58, "degrees C", 5, 95, "OK", "BMC", "2026-11-28T08:00:00+07:00"],
        ],
        "PI_Phases": [
            ["phase", "component", "responsibility", "classification"],
            *[[phase, phase, "Example phase", "firmware_architecture"] for phase in ("SEC", "PEI", "DXE", "BDS", "RT")],
        ],
        "Mixed_Unknown_Columns": [
            ["Device Label", "Vendor ID", "Product ID", "FW Build", "Temp_C", "MysteryMetric77", "Collected At", "Raw Payload"],
            ["Example USB Controller", "0x1234", "0x5678", "1.2.3-test", 39, 0.0042, "2026-11-28T08:00:00+07:00", '{"mode":"test"}'],
            ["Example PCI Device", "0x8086", "0x1234", "N/A", 44, 0.0051, "2026-11-28T08:01:00+07:00", '{"link_width":8}'],
        ],
        "Expected_Detection": [
            ["test_id", "test_objective", "expected_result", "severity"],
            *[
                [f"T{index:02}", f"Phase 1 objective {index}", "Verify behavior", "high"]
                for index in range(1, 14)
            ],
        ],
    }
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(name)
        for row in rows:
            worksheet.append(row)

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class HardwareWorkbookAnalysisTests(unittest.TestCase):
    def test_reads_sheets_independently_and_detects_phase_one_signatures(self):
        result = analyze_hardware_workbook(
            "HardwareAI_Phase1_System_Firmware_Test.xlsx",
            _phase_one_workbook(),
        )

        self.assertIsNotNone(result)
        assert result is not None
        ingestion = result["ingestion"]
        self.assertEqual(ingestion["total_sheets"], 10)
        self.assertEqual(ingestion["sheets_with_data"], 9)
        self.assertEqual(ingestion["sheets"][1]["header_row"], 3)
        self.assertEqual(ingestion["sheets"][1]["record_count"], 6)
        self.assertTrue(ingestion["source_preserved"])
        self.assertEqual(len(ingestion["source_file_sha256"]), 64)
        self.assertEqual(len(result["hardware_workbook_analysis"]["sheet_profiles"]), 8)
        self.assertEqual(result["analysis_type"], "system_hardware_firmware")
        self.assertEqual(
            ingestion["sheets"][2]["detected_standards"],
            ["UEFI"],
        )
        self.assertEqual(
            result["standards_detection"]["SMBIOS"]["structures"],
            [0, 1, 2, 3, 4, 17],
        )
        self.assertEqual(
            result["standards_detection"]["PI"]["phases"],
            ["SEC", "PEI", "DXE", "BDS", "RT"],
        )
        self.assertTrue(all(
            result["standards_detection"][standard]["detected"]
            for standard in (
                "SMBIOS", "DMI", "UEFI", "ACPI", "CIM", "Redfish", "IPMI", "PI"
            )
        ))
        self.assertEqual(
            result["hardware_workbook_analysis"]["sheet_profiles"][0][
                "column_mapping"
            ]["product_name"],
            "attributes.hardware.product_name",
        )
        profiles = {
            profile["source_sheet"]: profile["column_mapping"]
            for profile in result["hardware_workbook_analysis"]["sheet_profiles"]
        }
        self.assertEqual(
            profiles["UEFI_Firmware"]["variable_name"],
            "attributes.firmware.variable_name",
        )
        self.assertEqual(
            profiles["ACPI_Devices"]["power_state"],
            "attributes.acpi.power_state",
        )
        self.assertEqual(
            profiles["CIM_Inventory"]["property_name"],
            "attributes.cim.property_name",
        )
        self.assertEqual(
            profiles["Redfish_Thermal"]["reading"],
            "attributes.sensor_value",
        )
        self.assertEqual(
            profiles["Redfish_Thermal"]["resource_type"],
            "attributes.redfish.resource_type",
        )
        self.assertNotEqual(
            profiles["Redfish_Thermal"]["resource_type"],
            "attributes.acpi.resource_type",
        )
        self.assertEqual(
            profiles["IPMI_Sensors"]["sensor_number"],
            "attributes.ipmi.sensor_number",
        )
        phase_profile = next(
            profile
            for profile in result["hardware_workbook_analysis"]["sheet_profiles"]
            if profile["source_sheet"] == "PI_Phases"
        )
        self.assertEqual(
            phase_profile["column_mapping"]["phase"],
            "attributes.firmware.phase",
        )
        acpi_evidence = next(
            standard
            for standard in result["hardware_standards"]["detected_standards"]
            if standard["id"] == "ACPI"
        )["evidence"]
        self.assertIn(
            r"\_SB.PCI0.THM0",
            acpi_evidence[0]["matched_values"],
        )

        self.assertEqual(
            set(result["hardware_standards"]["detected_ids"]),
            {"SMBIOS", "DMI", "UEFI", "ACPI", "CIM", "REDFISH", "IPMI", "UEFI_PI"},
        )
        phase_report = result["hardware_workbook_analysis"]["phase1_test_report"]
        self.assertEqual(phase_report["total_count"], 13)
        self.assertEqual(phase_report["failed_count"], 0)
        self.assertEqual(phase_report["status"], "PASS")
        pi_evidence = next(
            item["evidence"]
            for item in phase_report["results"]
            if item["test_id"] == "T09"
        )
        self.assertIn("Canonical UEFI phases found:", pi_evidence)
        self.assertIn("additional worksheet phases:", pi_evidence)
        self.assertEqual(
            result["data_processing"]["rows_after_deduplication"],
            result["data_quality"]["data_rows"],
        )
        self.assertEqual(
            result["dataset_classification"]["analysis_type"],
            "system_hardware_firmware",
        )
        self.assertFalse(result["dataset_classification"]["analyses"]["quality"]["ready"])
        self.assertFalse(result["dataset_classification"]["analyses"]["machine"]["ready"])
        smbios_evidence = next(
            standard
            for standard in result["hardware_standards"]["detected_standards"]
            if standard["id"] == "SMBIOS"
        )["evidence"]
        self.assertEqual(
            set(smbios_evidence[0]["matched_values"]),
            {"0", "1", "2", "3", "4", "17"},
        )

    def test_keeps_unknown_fields_and_separates_product_id_from_product_name(self):
        result = analyze_hardware_workbook(
            "HardwareAI_Phase1_System_Firmware_Test.xlsx",
            _phase_one_workbook(),
        )

        assert result is not None
        mixed = next(
            profile
            for profile in result["hardware_workbook_analysis"]["sheet_profiles"]
            if profile["source_sheet"] == "Mixed_Unknown_Columns"
        )
        self.assertEqual(
            mixed["column_mapping"]["Product ID"],
            "attributes.hardware.identifiers.product_id",
        )
        self.assertNotEqual(
            mixed["column_mapping"]["Product ID"],
            "Product",
        )
        unknown = {field["source_column"] for field in mixed["unknown_fields"]}
        self.assertTrue({"MysteryMetric77", "Raw Payload"} <= unknown)
        self.assertNotIn("Temp_C", unknown)
        inferred = {
            field["source_column"]: field
            for field in mixed["unknown_fields"]
        }
        self.assertEqual(inferred["MysteryMetric77"]["inferred_type"], "number")
        self.assertIn("unconfirmed", inferred["MysteryMetric77"]["inferred_meaning"])
        self.assertEqual(inferred["Raw Payload"]["inferred_type"], "json")
        self.assertIn("unknown", inferred["Raw Payload"]["inferred_meaning"])
        self.assertTrue(result["hardware_workbook_analysis"]["raw_values_preserved"])
        self.assertEqual(
            result["ingestion"]["sheets"][-2]["unknown_fields"],
            mixed["unknown_fields"],
        )

    def test_normalizes_sensor_telemetry_and_smbios_physical_vs_firmware(self):
        result = analyze_hardware_workbook(
            "HardwareAI_Phase1_System_Firmware_Test.xlsx",
            _phase_one_workbook(),
        )

        assert result is not None
        structures = {
            entry["structure_type"]: entry["classification"]
            for entry in result["hardware_workbook_analysis"]["smbios_structures"]
        }
        self.assertEqual(structures["0"], ["firmware_metadata"])
        self.assertEqual(structures["1"], ["physical_inventory"])
        physical_types = {
            item.get("structure_type")
            for item in result["classification"]["physical_hardware"]
        }
        firmware_types = {
            item.get("structure_type")
            for item in result["classification"]["firmware"]
        }
        self.assertIn("1", physical_types)
        self.assertIn("0", firmware_types)
        self.assertNotIn("0", physical_types)

        readings = result["hardware_workbook_analysis"]["sensor_readings"]
        self.assertEqual(len(readings), 6)
        ipmi_reading = next(item for item in readings if item["source_standard"] == "IPMI")
        self.assertEqual(ipmi_reading["sensor_id"], "0x01")
        self.assertEqual(ipmi_reading["lower_critical"], 5)
        self.assertEqual(ipmi_reading["upper_critical"], 95)
        self.assertEqual(ipmi_reading["unit"], "Cel")
        self.assertEqual(ipmi_reading["health"], "OK")
        self.assertEqual(ipmi_reading["owner"], "BMC")
        redfish_cpu_readings = [
            item
            for item in readings
            if item["source_standard"] == "Redfish"
            and item.get("sensor_id") == "CPU_Temp"
        ]
        self.assertEqual(len(redfish_cpu_readings), 2)
        self.assertEqual(
            {item["timestamp"] for item in redfish_cpu_readings},
            {"2026-11-28T08:00:00+07:00", "2026-11-28T08:01:00+07:00"},
        )
        profile_sensors = result["hardware_profile"]["recognized_sensors"]
        self.assertTrue({
            "ACPI_Devices", "Redfish_Thermal", "IPMI_Sensors"
        } <= {
            item["source_sheet"]
            for item in profile_sensors
            if item.get("source_sheet") is not None
        })
        self.assertTrue(all(
            item.get("raw_value") is not None
            and item.get("raw_unit") is not None
            for item in readings
        ))
        classification = result["classification"]
        self.assertTrue(classification["monitoring_protocols"])
        self.assertEqual(
            {item["source_sheet"] for item in classification["hardware_interfaces"]},
            {"ACPI_Devices", "CIM_Inventory"},
        )
        self.assertEqual(
            {item["source_sheet"] for item in classification["monitoring_protocols"]},
            {"Redfish_Thermal", "IPMI_Sensors"},
        )
        self.assertEqual(result["data_quality"]["quality_score"], 100)
        self.assertEqual(result["data_quality"]["invalid_rows"], 0)

    def test_quality_report_counts_missing_invalid_duplicate_and_empty_rows(self):
        workbook_bytes = _phase_one_workbook()
        source = io.BytesIO(workbook_bytes)
        workbook = load_workbook(source)
        uefi = workbook["UEFI_Firmware"]
        uefi["E2"] = "maybe"
        uefi.insert_rows(3)
        acpi = workbook["ACPI_Devices"]
        acpi["G2"] = None
        ipmi = workbook["IPMI_Sensors"]
        ipmi.append([ipmi.cell(row=2, column=column).value for column in range(1, 11)])
        output = io.BytesIO()
        workbook.save(output)

        result = analyze_hardware_workbook(
            "HardwareAI_Phase1_System_Firmware_Test.xlsx",
            output.getvalue(),
        )
        assert result is not None
        quality = result["data_quality"]
        self.assertGreaterEqual(quality["empty_rows"], 1)
        self.assertEqual(quality["duplicate_rows"], 1)
        self.assertEqual(quality["invalid_values"]["boot_related"], 1)
        self.assertEqual(quality["invalid_rows"], 1)
        self.assertEqual(quality["missing_values"]["notes"], 1)

    def test_phase_one_report_fails_when_a_criterion_is_missing(self):
        workbook = load_workbook(io.BytesIO(_phase_one_workbook()))
        workbook["Expected_Detection"].delete_rows(14)
        output = io.BytesIO()
        workbook.save(output)

        result = analyze_hardware_workbook(
            "HardwareAI_Phase1_System_Firmware_Test.xlsx",
            output.getvalue(),
        )
        assert result is not None
        report = result["hardware_workbook_analysis"]["phase1_test_report"]
        self.assertEqual(report["total_count"], 13)
        self.assertEqual(report["status"], "FAIL")
        missing = next(item for item in report["results"] if item["test_id"] == "T13")
        self.assertEqual(missing["status"], "FAIL")
        self.assertIn("row is missing", missing["evidence"])

    def test_non_hardware_workbook_falls_back_to_existing_analysis(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["Product", "Production", "Defects"])
        worksheet.append(["Widget", 100, 3])
        output = io.BytesIO()
        workbook.save(output)

        self.assertIsNone(
            analyze_hardware_workbook("production.xlsx", output.getvalue())
        )


if __name__ == "__main__":
    unittest.main()
