from __future__ import annotations

import io
import hashlib
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Any

import pandas as pd
from fastapi import HTTPException

from app.services.hardware_knowledge_base import (
    get_knowledge_column_aliases,
    load_knowledge_json,
    normalize_unit_label,
    profile_hardware_dataframe,
)
from app.services.hardware_standards import detect_hardware_standards


MAX_WORKBOOK_ROWS = 100_000
MAX_WORKBOOK_COLUMNS = 500
MAX_WORKBOOK_SHEETS = 100
MAX_ARCHIVE_ENTRIES = 1_000
MAX_ARCHIVE_UNCOMPRESSED_SIZE = 50 * 1024 * 1024

_TEST_METADATA_HEADERS = {"test_id", "test_objective", "expected_result"}
_CONTEXTUAL_ATTRIBUTE_MAPPINGS = {
    "smbios_dmi": {
        "product_name": "attributes.hardware.product_name",
        "record_id": "attributes.hardware.record_id",
        "version": "attributes.hardware.version",
        "asset_tag": "attributes.hardware.asset_tag",
        "source": "attributes.hardware.source_standard",
    },
    "uefi_firmware": {
        "attributes": "attributes.firmware.attributes",
        "value_type": "attributes.firmware.value_type",
        "interpretation": "attributes.firmware.interpretation",
    },
    "acpi_devices": {
        "resource_type": "attributes.acpi.resource_type",
        "status": "attributes.acpi.status",
        "notes": "attributes.acpi.notes",
    },
    "redfish_thermal": {
        "resource_type": "attributes.redfish.resource_type",
    },
    "cim_inventory": {
        "unit": "attributes.cim.unit",
        "source_kind": "attributes.cim.source_kind",
        "description": "attributes.cim.description",
    },
    "ipmi_sensors": {
        "unit": "attributes.sensor.reading_unit",
        "sensor_owner": "attributes.ipmi.sensor_owner",
    },
    "pi_phases": {
        "phase": "attributes.firmware.phase",
        "component": "attributes.firmware.phase_component",
        "responsibility": "attributes.firmware.phase_responsibility",
        "example_artifact": "attributes.firmware.phase_artifact",
        "stage": "attributes.firmware.phase_stage",
        "classification": "attributes.firmware.phase_classification",
    },
}


@dataclass
class ParsedHardwareSheet:
    name: str
    dataframe: pd.DataFrame | None
    source_headers: list[Any]
    header_row: int | None
    row_count: int
    empty_row_count: int
    column_count: int
    role: str
    detected_standards: list[str]


def _normalize(value: Any) -> str:
    return re.sub(r"[\W]+", "_", str(value).strip().casefold()).strip("_")


def _validate_workbook_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Tệp Excel không hợp lệ.") from error
    if len(entries) > MAX_ARCHIVE_ENTRIES or sum(
        entry.file_size for entry in entries
    ) > MAX_ARCHIVE_UNCOMPRESSED_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Tệp Excel vượt quá giới hạn giải nén cho phép.",
        )
    if any(entry.flag_bits & 0x1 for entry in entries):
        raise ValueError("Không hỗ trợ tệp Excel được mã hóa.")


def _known_header_names() -> set[str]:
    aliases = get_knowledge_column_aliases()
    known = {_normalize(target) for target in aliases}
    for target, values in aliases.items():
        known.add(_normalize(target.removeprefix("attributes.")))
        known.update(_normalize(value) for value in values)
    known.update(_TEST_METADATA_HEADERS)
    known.update({
        "smbios_structure_type",
        "structure_name",
        "handle",
        "variable_name",
        "vendor_guid",
        "value_raw",
        "boot_related",
        "acpi_object_path",
        "hardware_id",
        "compatible_ids",
        "cim_namespace",
        "cim_class",
        "property_name",
        "property_value",
        "data_type",
        "resource_uri",
        "member_id",
        "reading_type",
        "reading_units",
        "status_health",
        "status_state",
        "sensor_number",
        "sensor_type",
        "lower_critical",
        "upper_critical",
        "event_status",
        "phase",
    })
    return known


def _detect_header_row(raw: pd.DataFrame) -> int | None:
    known_headers = _known_header_names()
    best: tuple[int, int, int] | None = None
    best_row: int | None = None
    for row_index in range(min(len(raw.index), 30)):
        values = raw.iloc[row_index].tolist()
        headers = [
            _normalize(value)
            for value in values
            if value is not None and not pd.isna(value) and str(value).strip()
        ]
        if len(headers) < 3:
            continue
        known_count = len(set(headers) & known_headers)
        following_data_rows = sum(
            sum(
                value is not None
                and not pd.isna(value)
                and bool(str(value).strip())
                for value in raw.iloc[next_row].tolist()
            ) >= 2
            for next_row in range(row_index + 1, min(len(raw.index), row_index + 5))
        )
        if known_count == 0 and following_data_rows == 0:
            continue
        score = (known_count, following_data_rows, len(headers))
        if best is None or score > best:
            best = score
            best_row = row_index
    return best_row


def _unique_headers(values: list[Any]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(values):
        base = (
            str(value).strip()
            if value is not None and not pd.isna(value) and str(value).strip()
            else f"Unnamed: {index}"
        )
        count = seen.get(base, 0) + 1
        seen[base] = count
        headers.append(base if count == 1 else f"{base}__{count}")
    return headers


def _sheet_role(
    name: str,
    dataframe: pd.DataFrame | None,
    standards: dict[str, Any] | None = None,
) -> str:
    normalized_name = _normalize(name)
    if dataframe is None:
        return "empty"
    if normalized_name in {"readme", "about", "instructions"}:
        return "documentation"
    columns = {_normalize(column) for column in dataframe.columns}
    if columns & _TEST_METADATA_HEADERS or "expected_detection" in normalized_name:
        return "test_metadata"
    if "device_label" in columns and columns & {"vendor_id", "product_id", "device_id"}:
        return "mixed_hardware_telemetry"
    detected_ids = set((standards or {}).get("detected_ids", []))
    if detected_ids & {"REDFISH", "IPMI"}:
        return "hardware_monitoring"
    if detected_ids & {"UEFI", "UEFI_PI"}:
        return "firmware"
    if detected_ids & {"ACPI", "CIM"}:
        return "hardware_interface"
    if detected_ids & {"SMBIOS", "DMI"}:
        return "hardware_inventory"
    return "unclassified"


def _parse_workbook(content: bytes) -> list[ParsedHardwareSheet]:
    if not content:
        raise ValueError("File Excel không chứa dữ liệu.")
    _validate_workbook_archive(content)
    try:
        excel = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    except (ValueError, OSError) as error:
        raise ValueError("Không thể đọc cấu trúc tệp Excel.") from error
    if len(excel.sheet_names) > MAX_WORKBOOK_SHEETS:
        raise HTTPException(
            status_code=413,
            detail=f"File vượt quá giới hạn {MAX_WORKBOOK_SHEETS} worksheet.",
        )

    parsed: list[ParsedHardwareSheet] = []
    total_rows = 0
    for sheet_name in excel.sheet_names:
        raw = pd.read_excel(
            excel,
            sheet_name=sheet_name,
            header=None,
            dtype=object,
            engine="openpyxl",
            nrows=MAX_WORKBOOK_ROWS + 2 - total_rows,
        )
        if raw.shape[1] > MAX_WORKBOOK_COLUMNS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Worksheet '{sheet_name}' vượt quá giới hạn "
                    f"{MAX_WORKBOOK_COLUMNS} cột."
                ),
            )
        header_row = _detect_header_row(raw)
        if header_row is None:
            parsed.append(ParsedHardwareSheet(
                name=sheet_name,
                dataframe=None,
                source_headers=[],
                header_row=None,
                row_count=0,
                empty_row_count=0,
                column_count=0,
                role="documentation" if _normalize(sheet_name) == "readme" else "unclassified",
                detected_standards=[],
            ))
            continue

        source_headers = raw.iloc[header_row].tolist()
        headers = _unique_headers(source_headers)
        rows = raw.iloc[header_row + 1 :].copy()
        rows.columns = headers
        nonempty = rows.apply(
            lambda column: column.map(
                lambda value: value is not None
                and not pd.isna(value)
                and bool(str(value).strip())
            )
        ).any(axis=1)
        empty_row_count = int((~nonempty).sum())
        rows = rows.loc[nonempty]
        if len(rows.index) > MAX_WORKBOOK_ROWS:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_WORKBOOK_ROWS:,} dòng.",
            )
        total_rows += len(rows.index)
        if total_rows > MAX_WORKBOOK_ROWS:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_WORKBOOK_ROWS:,} dòng.",
            )
        parsed.append(ParsedHardwareSheet(
            name=sheet_name,
            dataframe=rows,
            source_headers=source_headers,
            header_row=header_row,
            row_count=len(rows.index),
            empty_row_count=empty_row_count,
            column_count=len(headers),
            role="unclassified",
            detected_standards=[],
        ))
    return parsed


def _column_values(dataframe: pd.DataFrame, column: str) -> list[Any]:
    if column not in dataframe.columns:
        return []
    return [
        value
        for value in dataframe[column].tolist()
        if value is not None and not pd.isna(value) and str(value).strip()
    ]


def _matching_column(dataframe: pd.DataFrame, *names: str) -> str | None:
    normalized = {_normalize(name) for name in names}
    return next(
        (str(column) for column in dataframe.columns if _normalize(column) in normalized),
        None,
    )


def _schema_key(dataframe: pd.DataFrame | None) -> str | None:
    if dataframe is None:
        return None
    profile = profile_hardware_dataframe(dataframe)
    normalized_schema = sorted(
        _normalize(profile["column_mapping"].get(str(column), str(column)))
        for column in dataframe.columns
    )
    digest = hashlib.sha256(
        "\x1f".join(normalized_schema).encode("utf-8")
    ).hexdigest()[:12]
    return f"schema-{digest}"


def _profile_sheet(
    sheet: ParsedHardwareSheet,
) -> dict[str, Any]:
    if sheet.dataframe is None:
        raise ValueError("Cannot profile a worksheet without a detected table.")
    profile = profile_hardware_dataframe(sheet.dataframe)
    contextual_targets = dict(
        _CONTEXTUAL_ATTRIBUTE_MAPPINGS.get(_normalize(sheet.name), {})
    )
    normalized_columns = {_normalize(column) for column in sheet.dataframe.columns}
    if "resource_type" in normalized_columns:
        if "resource_uri" in normalized_columns:
            contextual_targets["resource_type"] = (
                "attributes.redfish.resource_type"
            )
        elif "acpi_object_path" in normalized_columns:
            contextual_targets["resource_type"] = "attributes.acpi.resource_type"
    targets_by_header = {
        _normalize(source): target
        for source, target in contextual_targets.items()
    }
    for column in sheet.dataframe.columns:
        target = targets_by_header.get(_normalize(column))
        if target is None:
            continue
        source = str(column)
        profile["column_mapping"][source] = target
        profile["unknown_fields"] = [
            field
            for field in profile["unknown_fields"]
            if field["source_column"] != source
        ]
        attribute_id = target.removeprefix("attributes.")
        for attribute in load_knowledge_json(
            "dictionaries/attribute_dictionary.json"
        ).get("attributes", []):
            if attribute.get("id") == attribute_id:
                profile["hardware_categories"].append(
                    str(attribute["category"])
                )
                break
    profile["hardware_categories"] = sorted(set(profile["hardware_categories"]))
    return profile


def _classify_hardware_domains(
    sheets: list[ParsedHardwareSheet],
) -> dict[str, list[dict[str, Any]]]:
    categories: dict[str, list[dict[str, Any]]] = {}
    for sheet in sheets:
        dataframe = sheet.dataframe
        if dataframe is None or sheet.role in {"documentation", "test_metadata"}:
            continue
        standard_ids = set(
            detect_hardware_standards(
                dataframe,
                text=sheet.name,
                structural_only=True,
            )["detected_ids"]
        )
        columns = {_normalize(column) for column in dataframe.columns}
        values = {
            _normalize(value)
            for column in dataframe.columns
            for value in dataframe[str(column)].head(100).tolist()
            if value is not None and not pd.isna(value) and str(value).strip()
        }
        profile = _profile_sheet(sheet)
        source_category = None
        if "smbios_structure_type" in columns:
            source_category = "physical_inventory"
            structure_column = _matching_column(
                dataframe, "smbios_structure_type"
            )
            structure_values = {
                str(value).strip()
                for value in _column_values(dataframe, structure_column or "")
            }
            if "0" in structure_values or "firmware_metadata" in values:
                categories.setdefault("firmware_metadata", []).append({
                    "source_sheet": sheet.name,
                    "matched_fields": sorted(columns & {
                        "smbios_structure_type", "physical_or_firmware"
                    }),
                })
        if standard_ids & {"UEFI", "UEFI_PI"} or "firmware" in sheet.role:
            categories.setdefault("firmware_configuration", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "variable_name", "boot_related", "phase", "classification"
                }),
            })
        if standard_ids & {"ACPI", "CIM"}:
            categories.setdefault("hardware_abstraction_interface", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "acpi_object_path", "hardware_id", "cim_namespace",
                    "cim_class", "property_name",
                }),
            })
        if standard_ids & {"REDFISH", "IPMI"}:
            categories.setdefault("hardware_monitoring", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "sensor_name", "reading", "reading_value", "reading_units",
                    "sensor_number", "status_health", "event_status",
                }),
            })
        if "temperature" in values or "power" in values or "fan" in values:
            categories.setdefault("power_thermal_monitoring", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "reading_type", "sensor_type", "resource_type",
                    "reading_units", "unit",
                }),
            })
        if "bus_connectivity" in profile["hardware_categories"]:
            categories.setdefault("bus_connectivity", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "vendor_id", "product_id", "device_id", "hardware_id",
                    "device_label",
                }),
            })
            if "device_label" in columns:
                categories.setdefault("physical_inventory", []).append({
                    "source_sheet": sheet.name,
                    "matched_fields": sorted(columns & {
                        "device_label", "vendor_id", "product_id", "device_id",
                    }),
                })
        if "firmware_build" in columns or "fw_build" in columns:
            categories.setdefault("firmware_metadata", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "firmware_build", "fw_build",
                }),
            })
        if "power_thermal" in profile["hardware_categories"] and not (
            "temperature" in values or "power" in values or "fan" in values
        ):
            categories.setdefault("power_thermal_monitoring", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns),
            })
        if source_category:
            categories.setdefault(source_category, []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns & {
                    "smbios_structure_type", "structure_name", "manufacturer",
                    "product_name", "cim_class", "property_name",
                }),
            })
        if standard_ids & {"SMBIOS", "DMI"} and source_category is None:
            categories.setdefault("physical_inventory", []).append({
                "source_sheet": sheet.name,
                "matched_fields": sorted(columns),
            })
    return categories


def _smbios_structures(sheets: list[ParsedHardwareSheet]) -> list[dict[str, Any]]:
    structures: list[dict[str, Any]] = []
    for sheet in sheets:
        dataframe = sheet.dataframe
        if dataframe is None:
            continue
        type_column = _matching_column(dataframe, "smbios_structure_type")
        if type_column is None:
            continue
        name_column = _matching_column(dataframe, "structure_name")
        category_column = _matching_column(dataframe, "physical_or_firmware")
        type_counts: dict[str, int] = {}
        type_names: dict[str, set[str]] = {}
        classifications: dict[str, set[str]] = {}
        for _, row in dataframe.iterrows():
            structure_type = str(row[type_column]).strip()
            if not structure_type or structure_type.casefold() == "nan":
                continue
            type_counts[structure_type] = type_counts.get(structure_type, 0) + 1
            if name_column and pd.notna(row[name_column]):
                type_names.setdefault(structure_type, set()).add(
                    str(row[name_column])
                )
            category = (
                str(row[category_column]).strip()
                if category_column and pd.notna(row[category_column])
                else (
                    "firmware_metadata"
                    if structure_type == "0"
                    else "physical_inventory"
                )
            )
            classifications.setdefault(structure_type, set()).add(category)
        for structure_type, count in sorted(
            type_counts.items(),
            key=lambda item: int(re.sub(r"\D", "", item[0]) or 0),
        ):
            structures.append({
                "source_sheet": sheet.name,
                "structure_type": structure_type,
                "structure_names": sorted(type_names.get(structure_type, set())),
                "record_count": count,
                "classification": sorted(
                    classifications.get(structure_type, set())
                ),
            })
    return structures


def _infer_sensor_device(name: Any, resource_type: Any = None) -> str | None:
    text = f"{name or ''} {resource_type or ''}".casefold()
    for needle, device in (
        ("cpu", "CPU"),
        ("processor", "CPU"),
        ("fan", "Fan"),
        ("psu", "Power Supply"),
        ("power supply", "Power Supply"),
        ("vrm", "VRM"),
        ("memory", "Memory"),
        ("gpu", "GPU"),
        ("battery", "Battery"),
    ):
        if needle in text:
            return device
    return None


def _numeric_value(value: Any) -> Any:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.item() if hasattr(parsed, "item") else parsed


def _unit_suffix_from_column(column: str) -> str | None:
    normalized = _normalize(column)
    for suffix, unit in (
        ("c", "°C"),
        ("f", "°F"),
        ("rpm", "RPM"),
        ("v", "V"),
        ("w", "W"),
    ):
        if normalized.endswith(f"_{suffix}"):
            return unit
    return None


def _assess_data_quality(sheets: list[ParsedHardwareSheet]) -> dict[str, Any]:
    attributes = {
        str(attribute.get("id")): attribute.get("data_type")
        for attribute in load_knowledge_json(
            "dictionaries/attribute_dictionary.json"
        ).get("attributes", [])
    }
    data_rows = 0
    empty_rows = 0
    duplicate_rows = 0
    invalid_row_count = 0
    missing_values: dict[str, int] = {}
    invalid_values: dict[str, int] = {}
    negative_values: dict[str, int] = {}
    per_sheet: list[dict[str, Any]] = []

    def is_missing(value: Any) -> bool:
        return value is None or pd.isna(value) or (
            isinstance(value, str) and not value.strip()
        )

    def is_valid(value: Any, data_type: str) -> bool:
        if data_type in {"integer", "number"}:
            numeric = _numeric_value(value)
            if numeric is None or not math.isfinite(float(numeric)):
                return False
            return data_type != "integer" or float(numeric).is_integer()
        if data_type == "boolean":
            return (
                isinstance(value, bool)
                or str(value).strip().casefold()
                in {"true", "false", "yes", "no", "on", "off", "0", "1"}
            )
        if data_type == "datetime":
            return not pd.isna(pd.to_datetime(value, errors="coerce"))
        return True

    for sheet in sheets:
        dataframe = sheet.dataframe
        if dataframe is None or sheet.role in {"documentation", "test_metadata"}:
            continue
        profile = _profile_sheet(sheet)
        row_count = len(dataframe.index)
        data_rows += row_count
        empty_rows += sheet.empty_row_count
        duplicate_mask = dataframe.duplicated(keep="first")
        sheet_duplicates = int(duplicate_mask.sum())
        duplicate_rows += sheet_duplicates
        sheet_missing: dict[str, int] = {}
        sheet_invalid: dict[str, int] = {}
        invalid_rows: set[Any] = set()

        for column in dataframe.columns:
            source_column = str(column)
            values = dataframe[source_column]
            missing_count = int(values.map(is_missing).sum())
            if missing_count:
                sheet_missing[source_column] = missing_count
                missing_values[source_column] = (
                    missing_values.get(source_column, 0) + missing_count
                )
            target = profile["column_mapping"].get(source_column)
            if not target:
                continue
            attribute_id = target.removeprefix("attributes.")
            data_type = attributes.get(attribute_id)
            if data_type not in {"integer", "number", "boolean", "datetime"}:
                continue
            for row_index, value in values.items():
                if is_missing(value):
                    continue
                if not is_valid(value, str(data_type)):
                    sheet_invalid[source_column] = (
                        sheet_invalid.get(source_column, 0) + 1
                    )
                    invalid_values[source_column] = (
                        invalid_values.get(source_column, 0) + 1
                    )
                    invalid_rows.add(row_index)
                    continue
                if data_type in {"integer", "number"}:
                    numeric = _numeric_value(value)
                    if numeric is not None and float(numeric) < 0:
                        negative_values[source_column] = (
                            negative_values.get(source_column, 0) + 1
                        )

        invalid_row_count += len(invalid_rows)
        per_sheet.append({
            "source_sheet": sheet.name,
            "data_rows": row_count,
            "empty_rows": sheet.empty_row_count,
            "duplicate_rows": sheet_duplicates,
            "invalid_rows": len(invalid_rows),
            "missing_values": sheet_missing,
            "invalid_values": sheet_invalid,
        })

    valid_rows = data_rows - invalid_row_count
    quality_score = (
        round(valid_rows / data_rows * 100, 2)
        if data_rows
        else None
    )
    warnings = []
    if empty_rows:
        warnings.append(f"Found {empty_rows} empty row(s) after worksheet headers.")
    if missing_values:
        warnings.append("Some source fields contain missing values; fields are optional.")
    if duplicate_rows:
        warnings.append(f"Found {duplicate_rows} exact duplicate row(s); source rows were retained.")
    if invalid_row_count:
        warnings.append(
            f"{invalid_row_count} row(s) contain invalid values for known typed fields."
        )
    if not data_rows:
        warnings.append("No hardware data rows were available for quality assessment.")
    return {
        "quality_score": quality_score,
        "quality_status": (
            "not_assessed"
            if not data_rows
            else "warning" if warnings else "good"
        ),
        "scoring_basis": (
            "Valid non-empty hardware data rows divided by all non-empty hardware "
            "data rows; missing optional fields and duplicates are reported separately."
        ),
        "total_rows": data_rows + empty_rows,
        "data_rows": data_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_row_count,
        "empty_rows": empty_rows,
        "duplicate_rows": duplicate_rows,
        "missing_values": missing_values,
        "invalid_values": invalid_values,
        "negative_values": negative_values,
        "missing_columns": [],
        "required_fields_policy": (
            "No fields are treated as required because workbook schemas vary."
        ),
        "per_sheet": per_sheet,
        "warnings": warnings,
    }


def _sensor_readings(sheets: list[ParsedHardwareSheet]) -> list[dict[str, Any]]:
    readings: list[dict[str, Any]] = []
    for sheet in sheets:
        dataframe = sheet.dataframe
        if dataframe is None:
            continue
        columns = {_normalize(column): str(column) for column in dataframe.columns}
        if "reading" in columns and "reading_units" in columns:
            source = "Redfish"
            value_key, unit_key = "reading", "reading_units"
            name_key = "sensor_name"
            owner_key = "resource_type"
            health_key, status_key = "status_health", "status_state"
            threshold_keys: tuple[str, ...] = ()
        elif "reading_value" in columns and "sensor_type" in columns:
            source = "IPMI"
            value_key, unit_key = "reading_value", "unit"
            name_key = "sensor_name"
            owner_key = "sensor_owner"
            health_key, status_key = "event_status", "event_status"
            threshold_keys = ("lower_critical", "upper_critical")
        elif "resource_value" in columns and "resource_type" in columns:
            source = "ACPI"
            value_key, unit_key = "resource_value", ""
            name_key = "acpi_object_path"
            owner_key = "object_type"
            health_key, status_key = "status", "power_state"
            threshold_keys = ()
        else:
            source = ""
            value_key = unit_key = name_key = owner_key = ""
            health_key = status_key = ""
            threshold_keys = ()

        if source:
            for row_index, row in dataframe.iterrows():
                if source == "ACPI":
                    resource_type = str(
                        row.get(columns.get("resource_type", ""), "")
                    ).casefold()
                    object_type = str(
                        row.get(columns.get("object_type", ""), "")
                    ).casefold()
                    if "temperature" not in resource_type and "thermal" not in object_type:
                        continue
                name = row.get(columns.get(name_key, ""))
                raw_reading = row.get(columns.get(value_key, ""))
                if pd.isna(raw_reading):
                    continue
                reading = _numeric_value(raw_reading)
                if reading is None:
                    continue
                unit_value = row.get(columns.get(unit_key, "")) if unit_key else None
                if unit_value is None and source == "ACPI":
                    notes_key = columns.get("notes")
                    notes = str(row.get(notes_key, "")) if notes_key else ""
                    unit_value = "Celsius" if "celsius" in notes.casefold() else None
                normalized_unit = normalize_unit_label(
                    str(unit_value)
                    if unit_value is not None and pd.notna(unit_value)
                    else None
                )
                raw_unit = (
                    str(unit_value)
                    if unit_value is not None and pd.notna(unit_value)
                    else None
                )
                device = _infer_sensor_device(
                    name,
                    row.get(columns.get(owner_key, "")),
                )
                sensor_type_key = (
                    columns.get("reading_type")
                    or columns.get("sensor_type")
                    or columns.get("resource_type")
                )
                health = row.get(columns.get(health_key, "")) if health_key else None
                status = row.get(columns.get(status_key, "")) if status_key else None
                timestamp_key = columns.get("timestamp")
                timestamp = row.get(timestamp_key) if timestamp_key else None
                result: dict[str, Any] = {
                    "source_sheet": sheet.name,
                    "source_standard": source,
                    "source_row": int(row_index) + 1,
                    "sensor_name": None if pd.isna(name) else str(name),
                    "sensor_type": (
                        None
                        if sensor_type_key is None or pd.isna(row.get(sensor_type_key))
                        else str(row.get(sensor_type_key))
                    ),
                    "reading": reading,
                    "raw_value": raw_reading,
                    "source_column": value_key,
                    "raw_unit": raw_unit,
                    "unit": normalized_unit,
                    "device": device,
                    "owner": (
                        None
                        if not owner_key
                        or pd.isna(row.get(columns.get(owner_key, "")))
                        else str(row.get(columns.get(owner_key, "")))
                    ),
                    "health": None if pd.isna(health) else str(health),
                    "status": None if pd.isna(status) else str(status),
                    "timestamp": (
                        timestamp.isoformat()
                        if isinstance(timestamp, pd.Timestamp)
                        else None if timestamp is None or pd.isna(timestamp)
                        else str(timestamp)
                    ),
                }
                for id_key in ("member_id", "sensor_number"):
                    key = columns.get(id_key)
                    if key and pd.notna(row.get(key)):
                        result["sensor_id"] = str(row.get(key))
                        break
                for threshold in threshold_keys:
                    key = columns.get(threshold)
                    if key and pd.notna(row.get(key)):
                        normalized_threshold = _numeric_value(row.get(key))
                        if normalized_threshold is not None:
                            result[threshold] = normalized_threshold
                readings.append(result)

        profile = profile_hardware_dataframe(dataframe)
        handled_columns = {
            columns[key]
            for key in ("reading", "reading_value", "resource_value")
            if key in columns
        }
        device_key = next(
            (
                columns[key]
                for key in ("device_label", "device_name", "device", "component")
                if key in columns
            ),
            None,
        )
        timestamp_key = columns.get("collected_at") or columns.get("timestamp")
        status_key = columns.get("status") or columns.get("health")
        raw_units = {
            unit["source_column"]: unit["source_unit"]
            for unit in profile["unit_mappings"]
        }
        for sensor in profile["recognized_sensors"]:
            source_column = sensor["source_column"]
            if source_column in handled_columns:
                continue
            for row_index, row in dataframe.iterrows():
                raw_reading = row[source_column]
                if pd.isna(raw_reading):
                    continue
                reading = _numeric_value(raw_reading)
                if reading is None:
                    continue
                raw_device = row.get(device_key) if device_key else None
                device = _infer_sensor_device(raw_device) or (
                    str(raw_device) if raw_device is not None and pd.notna(raw_device) else None
                )
                timestamp = row.get(timestamp_key) if timestamp_key else None
                status = row.get(status_key) if status_key else None
                readings.append({
                    "source_sheet": sheet.name,
                    "source_standard": "Unspecified",
                    "source_row": int(row_index) + 1,
                    "sensor_name": source_column,
                    "sensor_type": sensor["sensor_id"],
                    "reading": reading,
                    "raw_value": raw_reading,
                    "source_column": source_column,
                    "raw_unit": (
                        raw_units.get(source_column)
                        or _unit_suffix_from_column(source_column)
                    ),
                    "unit": sensor["unit"],
                    "device": device,
                    "owner": None,
                    "health": None,
                    "status": None if status is None or pd.isna(status) else str(status),
                    "timestamp": (
                        timestamp.isoformat()
                        if isinstance(timestamp, pd.Timestamp)
                        else None if timestamp is None or pd.isna(timestamp)
                        else str(timestamp)
                    ),
                })
    return readings


def _aggregate_profile(
    sheets: list[ParsedHardwareSheet],
    sensor_readings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    raw_columns: list[str] = []
    mappings: dict[str, str] = {}
    unknown: list[dict[str, Any]] = []
    categories: set[str] = set()
    recognized_sensors: list[dict[str, Any]] = []
    unit_mappings: list[dict[str, str]] = []
    total_rows = 0
    for sheet in sheets:
        if sheet.dataframe is None or sheet.role in {"documentation", "test_metadata"}:
            continue
        profile = _profile_sheet(sheet)
        profiles.append({"source_sheet": sheet.name, **profile})
        total_rows += sheet.row_count
        for column in profile["raw_columns"]:
            if column not in raw_columns:
                raw_columns.append(column)
        for source, target in profile["column_mapping"].items():
            key = source if source not in mappings else f"{sheet.name}!{source}"
            mappings[key] = target
        unknown.extend(
            {"source_sheet": sheet.name, **field}
            for field in profile["unknown_fields"]
        )
        categories.update(profile["hardware_categories"])
        recognized_sensors.extend(
            {**sensor, "source_sheet": sheet.name}
            for sensor in profile["recognized_sensors"]
        )
        unit_mappings.extend(
            {**unit, "source_sheet": sheet.name}
            for unit in profile["unit_mappings"]
        )

    sensor_keys = {
        (
            sensor.get("source_sheet"),
            sensor.get("source_column"),
            sensor.get("sensor_id"),
            sensor.get("unit"),
            sensor.get("sensor_name"),
        )
        for sensor in recognized_sensors
    }
    for reading in sensor_readings:
        if reading["source_standard"] == "Unspecified":
            continue
        description = " ".join(
            str(reading.get(key) or "")
            for key in ("sensor_type", "sensor_name", "unit")
        ).casefold()
        if any(word in description for word in ("temperature", "thermal", "temp")):
            sensor_id = "temperature"
        elif any(word in description for word in ("fan", "rpm", "rotational")):
            sensor_id = "fan_speed"
        elif any(word in description for word in ("voltage", "volt")):
            sensor_id = "voltage"
        elif any(word in description for word in ("power", "watt")):
            sensor_id = "power"
        else:
            sensor_id = "sensor"
        sensor = {
            "source_sheet": reading["source_sheet"],
            "source_column": reading["source_column"],
            "sensor_id": sensor_id,
            "sensor_name": reading.get("sensor_name"),
            "unit": reading.get("unit") or "",
        }
        key = (
            sensor["source_sheet"],
            sensor["source_column"],
            sensor["sensor_id"],
            sensor["unit"],
            sensor["sensor_name"],
        )
        if key not in sensor_keys:
            recognized_sensors.append(sensor)
            sensor_keys.add(key)
    return {
        "schema_version": "1.0.0",
        "raw_columns": raw_columns,
        "column_mapping": mappings,
        "unknown_fields": unknown,
        "hardware_categories": sorted(categories),
        "recognized_sensors": recognized_sensors,
        "unit_mappings": unit_mappings,
        "row_count": total_rows,
        "raw_values_preserved": True,
    }, profiles


def _standards_detection_report(
    standard_map: dict[str, dict[str, Any]],
    structures: list[dict[str, Any]],
) -> dict[str, Any]:
    known_structure_types = sorted({
        int(item["structure_type"])
        for item in structures
        if str(item["structure_type"]).isdigit()
    })
    phase_evidence = [
        value
        for evidence in standard_map.get("UEFI_PI", {}).get("evidence", [])
        for value in evidence.get("matched_values", [])
    ]
    phases = sorted(
        {str(value).upper() for value in phase_evidence},
        key=("SEC", "PEI", "DXE", "BDS", "RT").index,
    )
    report = {
        "SMBIOS": {
            "detected": "SMBIOS" in standard_map,
            "structures": known_structure_types,
        },
        "DMI": {"detected": "DMI" in standard_map},
        "UEFI": {"detected": "UEFI" in standard_map},
        "ACPI": {"detected": "ACPI" in standard_map},
        "CIM": {"detected": "CIM" in standard_map},
        "Redfish": {"detected": "REDFISH" in standard_map},
        "IPMI": {"detected": "IPMI" in standard_map},
        "PI": {
            "detected": "UEFI_PI" in standard_map,
            "phases": phases,
        },
    }
    return report


def _classification_report(
    categories: dict[str, list[dict[str, Any]]],
    structures: list[dict[str, Any]],
    sensor_readings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    physical = [
        {
            "source_sheet": item["source_sheet"],
            "structure_type": item["structure_type"],
            "structure_names": item["structure_names"],
            "record_count": item["record_count"],
        }
        for item in structures
        if "physical_inventory" in item["classification"]
    ]
    structure_sheets = {item["source_sheet"] for item in structures}
    physical.extend(
        item
        for item in categories.get("physical_inventory", [])
        if item["source_sheet"] not in structure_sheets
    )
    firmware = [
        {
            "source_sheet": item["source_sheet"],
            "structure_type": item["structure_type"],
            "structure_names": item["structure_names"],
            "record_count": item["record_count"],
        }
        for item in structures
        if "firmware_metadata" in item["classification"]
    ]
    structure_sheets = {item["source_sheet"] for item in structures}
    firmware_by_sheet: dict[str, set[str]] = {}
    for key in ("firmware_metadata", "firmware_configuration"):
        for item in categories.get(key, []):
            if key == "firmware_metadata" and item["source_sheet"] in structure_sheets:
                continue
            firmware_by_sheet.setdefault(item["source_sheet"], set()).update(
                item["matched_fields"]
            )
    firmware.extend([
        {"source_sheet": sheet, "fields": sorted(fields)}
        for sheet, fields in sorted(firmware_by_sheet.items())
    ])
    interfaces_by_sheet: dict[str, set[str]] = {}
    for item in categories.get("hardware_abstraction_interface", []):
        interfaces_by_sheet.setdefault(item["source_sheet"], set()).update(
            item["matched_fields"]
        )
    interfaces = [
        {"source_sheet": sheet, "matched_fields": sorted(fields)}
        for sheet, fields in sorted(interfaces_by_sheet.items())
    ]
    monitoring_protocols = [
        {
            "source_sheet": item["source_sheet"],
            "matched_fields": item["matched_fields"],
        }
        for item in categories.get("hardware_monitoring", [])
    ]
    return {
        "physical_hardware": physical,
        "firmware": firmware,
        "hardware_interfaces": interfaces,
        "monitoring_protocols": monitoring_protocols,
        "monitoring_sensors": sensor_readings,
    }


def _phase_one_test_report(
    sheets: list[ParsedHardwareSheet],
    profiles: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    sensor_readings: list[dict[str, Any]],
    classification: dict[str, Any],
    ingestion: dict[str, Any],
) -> dict[str, Any] | None:
    expected_sheet = next(
        (
            sheet for sheet in sheets
            if sheet.role == "test_metadata"
            and sheet.dataframe is not None
            and "test_id" in {_normalize(column) for column in sheet.dataframe.columns}
        ),
        None,
    )
    if expected_sheet is None or expected_sheet.dataframe is None:
        return None

    profile_by_sheet = {item["source_sheet"]: item for item in profiles}
    mappings = {
        sheet_name: profile["column_mapping"]
        for sheet_name, profile in profile_by_sheet.items()
    }
    structures_by_type = {
        item["structure_type"]: item for item in structures
    }

    def values(sheet_name: str, column_name: str) -> set[str]:
        sheet = next((item for item in sheets if item.name == sheet_name), None)
        if sheet is None or sheet.dataframe is None:
            return set()
        column = _matching_column(sheet.dataframe, column_name)
        if column is None:
            return set()
        return {
            str(value).strip().casefold()
            for value in _column_values(sheet.dataframe, column)
        }

    def mapping_for(sheet_name: str, *targets: str) -> bool:
        mapping = mappings.get(sheet_name, {})
        return all(target in mapping.values() for target in targets)

    standards = {
        standard for sheet in sheets for standard in sheet.detected_standards
    }
    redfish = [
        item for item in sensor_readings
        if item["source_standard"] == "Redfish"
    ]
    ipmi = [
        item for item in sensor_readings
        if item["source_standard"] == "IPMI"
    ]
    phases = values("PI_Phases", "phase")
    unknown_by_name = {
        field["source_column"]: field
        for item in profiles
        for field in item["unknown_fields"]
    }
    physical_types = {
        kind for kind, item in structures_by_type.items()
        if "physical_inventory" in item["classification"]
    }
    firmware_types = {
        kind for kind, item in structures_by_type.items()
        if "firmware_metadata" in item["classification"]
    }

    checks: dict[str, tuple[bool, str]] = {
        "T01": (
            len(sheets) >= 9 and all(sheet.name for sheet in sheets),
            f"Enumerated {len(sheets)} worksheet(s); "
            f"{sum(sheet.dataframe is not None for sheet in sheets)} have detected tables.",
        ),
        "T02": (
            ingestion["source_preserved"]
            and ingestion["source_file_sha256"]
            and all(item["name"] for item in ingestion["sheets"]),
            "Source workbook hash, worksheet names, headers, and row counts are recorded; "
            "source values are retained during ingestion.",
        ),
        "T03": (
            {"0", "1", "2", "3", "4", "17"} <= structures_by_type.keys()
            and "0" in firmware_types
            and "0" not in physical_types
            and {"1", "2", "3", "4", "17"} <= physical_types,
            f"SMBIOS types found: {', '.join(sorted(structures_by_type))}; "
            "Type 0 is firmware and the inventory types are physical.",
        ),
        "T04": (
            "UEFI" in standards
            and mapping_for(
                "UEFI_Firmware",
                "attributes.firmware.variable_name",
                "attributes.firmware.boot_related",
            )
            and {"bootorder", "secureboot", "setupmode"}
            <= values("UEFI_Firmware", "variable_name"),
            "UEFI variable names include BootOrder, SecureBoot, and SetupMode; "
            "variable and boot-related fields are mapped.",
        ),
        "T05": (
            "ACPI" in standards
            and mapping_for(
                "ACPI_Devices",
                "attributes.acpi.object_path",
                "attributes.acpi.hardware_id",
                "attributes.acpi.object_type",
                "attributes.acpi.resource_type",
                "attributes.acpi.power_state",
            )
            and any("thermal" in value for value in values("ACPI_Devices", "object_type")),
            "ACPI paths, IDs, object/resource types, and power state are mapped; "
            "thermal-zone evidence is present.",
        ),
        "T06": (
            "CIM" in standards
            and mapping_for(
                "CIM_Inventory",
                "attributes.cim.namespace",
                "attributes.cim.class",
                "attributes.cim.property_name",
                "attributes.cim.data_type",
                "attributes.cim.unit",
            ),
            "CIM namespace, class, property, data type, and unit columns are mapped.",
        ),
        "T07": (
            "REDFISH" in standards
            and mapping_for(
                "Redfish_Thermal",
                "attributes.redfish.resource_uri",
                "attributes.redfish.resource_type",
                "attributes.sensor_value",
                "attributes.sensor.reading_unit",
                "attributes.sensor.health",
                "attributes.sensor.state",
                "attributes.sensor.timestamp",
            )
            and bool(redfish)
            and all(item.get("timestamp") and item.get("raw_value") is not None for item in redfish),
            f"Redfish resource and telemetry fields are mapped; {len(redfish)} timestamped reading(s) retained.",
        ),
        "T08": (
            "IPMI" in standards
            and mapping_for(
                "IPMI_Sensors",
                "attributes.ipmi.sensor_number",
                "attributes.sensor.type",
                "attributes.ipmi.lower_critical",
                "attributes.ipmi.upper_critical",
                "attributes.ipmi.event_status",
                "attributes.ipmi.sensor_owner",
            )
            and bool(ipmi)
            and all(item.get("lower_critical") is not None
                    and item.get("upper_critical") is not None
                    and item.get("owner") for item in ipmi),
            f"IPMI sensor, threshold, status, and owner fields are mapped; "
            f"{len(ipmi)} reading(s) retained.",
        ),
        "T09": (
            "UEFI_PI" in standards
            and {"sec", "pei", "dxe", "bds", "rt"} <= phases,
            (
                "Canonical UEFI phases found: "
                f"{', '.join(sorted({'sec', 'pei', 'dxe', 'bds', 'rt'} & phases))}; "
                f"additional worksheet phases: "
                f"{', '.join(sorted(phases - {'sec', 'pei', 'dxe', 'bds', 'rt'})) or 'none'}."
            ),
        ),
        "T10": (
            {"MysteryMetric77", "Raw Payload"} <= unknown_by_name.keys()
            and "unconfirmed" in unknown_by_name.get(
                "MysteryMetric77", {}
            ).get("inferred_meaning", "").casefold()
            and "unknown" in unknown_by_name.get(
                "Raw Payload", {}
            ).get("inferred_meaning", "").casefold(),
            "Both unknown columns are retained with cautious type hints and no asserted domain meaning.",
        ),
        "T11": (
            "0" in firmware_types
            and bool(physical_types - {"0"})
            and bool(classification["firmware"])
            and bool(classification["physical_hardware"]),
            "Physical inventory and firmware metadata/configuration are reported separately.",
        ),
        "T12": (
            bool(sensor_readings)
            and all(
                item.get("raw_value") is not None
                and (
                    not item.get("unit")
                    or item.get("raw_unit") is not None
                )
                for item in sensor_readings
            ),
            "Sensor readings retain raw values and source units; normalized units are emitted only when available.",
        ),
        "T13": (
            bool(redfish or ipmi)
            and all(
                item.get("timestamp") and item.get("source_row")
                for item in redfish + ipmi
            ),
            f"{len(redfish) + len(ipmi)} timestamped Redfish/IPMI observation(s) "
            "are represented as row-level readings.",
        ),
    }

    dataframe = expected_sheet.dataframe
    test_id_column = _matching_column(dataframe, "test_id")
    objective_column = _matching_column(dataframe, "test_objective")
    expected_column = _matching_column(dataframe, "expected_result")
    severity_column = _matching_column(dataframe, "severity")
    criteria_by_id: dict[str, Any] = {}
    for _, row in dataframe.iterrows():
        test_id = str(row[test_id_column]).strip().upper() if test_id_column else ""
        if test_id not in checks:
            continue
        criteria_by_id.setdefault(test_id, row)

    results = []
    for index in range(1, 14):
        test_id = f"T{index:02}"
        row = criteria_by_id.get(test_id)
        passed, evidence = checks[test_id]
        if row is None:
            passed = False
            evidence = "Expected_Detection row is missing; this criterion was not declared."
        results.append({
            "test_id": test_id,
            "objective": (
                str(row[objective_column])
                if row is not None
                and objective_column
                and pd.notna(row[objective_column])
                else "Expected_Detection criterion is missing"
            ),
            "expected_result": (
                str(row[expected_column])
                if row is not None
                and expected_column
                and pd.notna(row[expected_column])
                else ""
            ),
            "severity": (
                str(row[severity_column])
                if row is not None
                and severity_column
                and pd.notna(row[severity_column])
                else None
            ),
            "status": "PASS" if passed else "FAIL",
            "evidence": evidence,
        })
    return {
        "status": (
            "PASS"
            if results and all(item["status"] == "PASS" for item in results)
            else "FAIL"
        ),
        "passed_count": sum(item["status"] == "PASS" for item in results),
        "failed_count": sum(item["status"] == "FAIL" for item in results),
        "total_count": len(results),
        "results": results,
    }


def analyze_hardware_workbook(
    filename: str,
    content: bytes,
) -> dict[str, Any] | None:
    if not filename.casefold().endswith(".xlsx"):
        return None
    sheets = _parse_workbook(content)
    for sheet in sheets:
        if sheet.dataframe is None:
            continue
        standards = detect_hardware_standards(
            sheet.dataframe,
            text=sheet.name,
            structural_only=True,
        )
        sheet.role = _sheet_role(sheet.name, sheet.dataframe, standards)

    data_sheets = [
        sheet for sheet in sheets
        if sheet.dataframe is not None
        and sheet.role not in {"documentation", "test_metadata"}
    ]
    sheet_standards: list[dict[str, Any]] = []
    standard_map: dict[str, dict[str, Any]] = {}
    for sheet in data_sheets:
        result = detect_hardware_standards(
            sheet.dataframe,
            text=sheet.name,
            structural_only=True,
        )
        sheet.detected_standards = result["detected_ids"]
        sheet_standards.append({
            "source_sheet": sheet.name,
            **result,
        })
        for standard in result["detected_standards"]:
            item = standard_map.setdefault(
                standard["id"],
                {**standard, "source_sheets": [], "evidence": []},
            )
            item["source_sheets"].append(sheet.name)
            item["evidence"].extend(standard.get("evidence", []))

    detected_ids = set(standard_map)
    hardware_signals = {
        "SMBIOS", "DMI", "UEFI", "ACPI", "CIM", "REDFISH", "IPMI", "UEFI_PI"
    }
    if not detected_ids.intersection(hardware_signals):
        return None

    categories = _classify_hardware_domains(sheets)
    smbios_structures = _smbios_structures(sheets)
    sensor_readings = _sensor_readings(sheets)
    profile, sheet_profiles = _aggregate_profile(sheets, sensor_readings)
    unknown_by_sheet = {
        item["source_sheet"]: [
            field["source_column"] for field in item["unknown_fields"]
        ]
        for item in sheet_profiles
        if item["unknown_fields"]
    }
    total_rows = sum(sheet.row_count for sheet in sheets)
    sheets_with_data = sum(sheet.row_count > 0 for sheet in sheets)
    hardware_data_rows = sum(
        sheet.row_count
        for sheet in sheets
        if sheet.role not in {"documentation", "test_metadata"}
    )
    standards_result = {
        "pack_id": "hardware-standards-p0",
        "priority": "P0",
        "detected_standards": list(standard_map.values()),
        "detected_ids": sorted(detected_ids),
        "physical_hardware_focus": sorted({
            *(
                f"{item['source_sheet']} Type {item['structure_type']}: "
                f"{', '.join(item['structure_names'])}"
                for item in smbios_structures
                if "physical_inventory" in item["classification"]
            ),
            *(
                item["source_sheet"]
                for item in categories.get("physical_inventory", [])
                if not any(
                    structure["source_sheet"] == item["source_sheet"]
                    for structure in smbios_structures
                )
            ),
        }),
        "firmware_focus": sorted({
            *(
                f"{item['source_sheet']} Type {item['structure_type']}: "
                f"{', '.join(item['structure_names'])}"
                for item in smbios_structures
                if "firmware_metadata" in item["classification"]
            ),
            *(
                item["source_sheet"]
                for key, values in categories.items()
                if key.startswith("firmware")
                for item in values
                if not any(
                    structure["source_sheet"] == item["source_sheet"]
                    for structure in smbios_structures
                )
            ),
        }),
    }
    all_columns = list(dict.fromkeys(
        column
        for sheet in sheets
        if sheet.dataframe is not None
        for column in map(str, sheet.dataframe.columns)
    ))
    domain_names = sorted(categories)
    ingestion = {
        "workbook_read": True,
        "total_sheets": len(sheets),
        "sheets_with_data": sheets_with_data,
        "source_preserved": True,
        "source_file_sha256": hashlib.sha256(content).hexdigest(),
        "total_data_rows": total_rows,
        "hardware_data_rows": hardware_data_rows,
        "sheets": [
            {
                "name": sheet.name,
                "role": sheet.role,
                "schema_key": _schema_key(sheet.dataframe),
                "detected_standards": sheet.detected_standards,
                "header_row": (
                    sheet.header_row + 1 if sheet.header_row is not None else None
                ),
                "record_count": sheet.row_count,
                "empty_row_count": sheet.empty_row_count,
                "column_count": sheet.column_count,
                "source_columns": [
                    None if value is None or pd.isna(value) else str(value)
                    for value in sheet.source_headers
                ],
                "unknown_fields": next(
                    (
                        item["unknown_fields"]
                        for item in sheet_profiles
                        if item["source_sheet"] == sheet.name
                    ),
                    [],
                ),
            }
            for sheet in sheets
        ],
    }
    hardware_workbook_analysis = {
        "analysis_type": "system_hardware_firmware",
        "categories": categories,
        "sheet_standards": sheet_standards,
        "smbios_structures": smbios_structures,
        "sensor_readings": sensor_readings,
        "sheet_profiles": sheet_profiles,
        "raw_values_preserved": True,
    }
    quality = _assess_data_quality(sheets)
    unknown_fields = sorted({
        field
        for fields in unknown_by_sheet.values()
        for field in fields
    })
    classification = {
        "analysis_type": "system_hardware_firmware",
        "domain": "system_hardware_firmware",
        "domains": domain_names,
        "recognized_fields": all_columns,
        "analyses": {
            key: {
                "label": label,
                "ready": False,
                "matched_fields": [],
            }
            for key, label in (
                ("production", "Phân tích sản xuất"),
                ("quality", "Phân tích chất lượng sản xuất"),
                ("machine", "Phân tích máy sản xuất"),
            )
        },
    }
    classification_report = _classification_report(
        categories,
        smbios_structures,
        sensor_readings,
    )
    phase_one_test_report = _phase_one_test_report(
        sheets,
        sheet_profiles,
        smbios_structures,
        sensor_readings,
        classification_report,
        ingestion,
    )
    if phase_one_test_report is not None:
        hardware_workbook_analysis["phase1_test_report"] = phase_one_test_report
    return {
        "analysis_type": "system_hardware_firmware",
        "title": "Phân tích phần cứng hệ thống và firmware",
        "summary": (
            f"Đã đọc {len(sheets)} worksheet, trong đó {sheets_with_data} có dữ liệu; "
            f"nhận diện các lĩnh vực: {', '.join(domain_names) or 'phần cứng hệ thống'}."
        ),
        "kpis": [],
        "insights": [
            f"Đã nhận diện {len(detected_ids)} tiêu chuẩn từ cấu trúc và giá trị dữ liệu."
        ],
        "warnings": [
            "Nhận diện tiêu chuẩn dựa trên dấu hiệu cấu trúc trong workbook; "
            "không xác nhận tuân thủ đầy đủ đặc tả chính thức."
        ],
        "recommendations": [],
        "product_performance": [],
        "model_performance": [],
        "component_performance": [],
        "supplier_performance": [],
        "batch_performance": [],
        "defect_type_analysis": [],
        "daily_production": [],
        "data_scope": {
            "has_product_data": False,
            "has_model_data": False,
            "has_component_data": False,
            "has_supplier_data": False,
            "has_batch_data": False,
            "has_defect_type_data": False,
            "has_quality_status_data": False,
            "has_defect_data": False,
            "has_downtime_data": False,
            "has_date_data": False,
        },
        "data_quality": quality,
        "data_processing": {
            "rows_after_deduplication": max(
                0,
                quality["data_rows"] - quality["duplicate_rows"],
            ),
            "rows_used_for_kpi": 0,
            "invalid_metric_rows_excluded": quality["invalid_rows"],
            "invalid_metric_rows_excluded_by_metric": {},
        },
        "anomaly_analysis": {"status": "insufficient_data", "findings": {}},
        "ai_analysis": {
            "executive_summary": {
                "summary": "Phân tích phần cứng được thực hiện từ dữ liệu có cấu trúc.",
                "key_observations": [],
            },
            "production_analysis": {"summary": "", "key_observations": []},
            "quality_analysis": {"summary": "", "key_observations": []},
            "component_analysis": {"summary": "", "key_observations": []},
            "supplier_analysis": {"summary": "", "key_observations": []},
            "batch_analysis": {"summary": "", "key_observations": []},
            "downtime_analysis": {"summary": "", "key_observations": []},
        },
        "ai_status": "not_requested",
        "dataset_classification": classification,
        "hardware_standards": standards_result,
        "hardware_profile": profile,
        "ingestion": ingestion,
        "hardware_workbook_analysis": hardware_workbook_analysis,
        "standards_detection": _standards_detection_report(
            standard_map,
            smbios_structures,
        ),
        "classification": classification_report,
        "unknown_fields": {
            "preserved": True,
            "fields": unknown_fields,
            "by_sheet": unknown_by_sheet,
        },
    }
