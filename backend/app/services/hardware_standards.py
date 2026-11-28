from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd

from app.services.hardware_knowledge_base import load_knowledge_json


HARDWARE_STANDARDS_PRIORITY = "P0"


def _normalize(value: Any) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[\s\-_/]+", " ", text)
    text = re.sub(r"[^a-z0-9\u00c0-\u024f ]", "", text)
    return text


def _standards() -> list[dict[str, Any]]:
    data = load_knowledge_json("metadata/standards.json")
    standards = data.get("standards")
    sources_data = load_knowledge_json("metadata/sources.json")
    sources = {
        source["id"]: source
        for source in sources_data.get("sources", [])
        if isinstance(source, dict) and isinstance(source.get("id"), str)
    }
    if not isinstance(standards, list):
        raise ValueError("Standards metadata must contain a standards list.")
    resolved: list[dict[str, Any]] = []
    for standard in standards:
        if not isinstance(standard, dict):
            raise ValueError("Each standards metadata item must be an object.")
        source = sources.get(standard.get("source_id"))
        if source is None:
            raise ValueError(
                f"Missing source metadata for standard {standard.get('id')}."
            )
        resolved.append({**standard, "source": source})
    return resolved


def get_hardware_standards_pack() -> dict[str, Any]:
    standards = []
    for standard in _standards():
        standards.append({
            "id": standard["id"],
            "name": standard["name"],
            "full_name": standard["full_name"],
            "priority": HARDWARE_STANDARDS_PRIORITY,
            "domains": standard["domains"],
            "attributes": standard["attributes"],
            "source": {
                "name": standard["source"]["name"],
                "url": standard["source"]["url"],
                "status": standard["source"]["status"],
                "version": standard["source"].get("version"),
            },
        })
    licenses = load_knowledge_json("metadata/licenses.json")
    sources = load_knowledge_json("metadata/sources.json")
    return {
        "pack_id": "hardware-standards-p0",
        "pack_name": "Hardware Standards & Knowledge Pack",
        "priority": HARDWARE_STANDARDS_PRIORITY,
        "knowledge_base_version": load_knowledge_json(
            "metadata/versions.json"
        )["pack_version"],
        "standards": standards,
        "sources": sources["sources"],
        "license_review": licenses,
    }


def detect_hardware_standards(
    dataframe: pd.DataFrame | None = None,
    *,
    text: str | Iterable[str] | None = None,
    structural_only: bool = False,
) -> dict[str, Any]:
    candidates: list[str] = []
    evidence_by_id: dict[str, list[dict[str, Any]]] = {}
    if dataframe is not None:
        candidates.extend(str(column) for column in dataframe.columns)
        evidence_by_id = _detect_structural_signatures(dataframe)
    if text is not None:
        candidates.extend([text] if isinstance(text, str) else text)

    normalized_candidates = [_normalize(candidate) for candidate in candidates]
    detected: list[dict[str, Any]] = []

    for standard in _standards():
        evidence = evidence_by_id.get(str(standard["id"]), [])
        matched_aliases = sorted({
            alias
            for alias in standard["aliases"]
            if any(
                re.search(
                    rf"(?<![a-z0-9]){re.escape(_normalize(alias))}(?![a-z0-9])",
                    candidate,
                )
                for candidate in normalized_candidates
            )
        })
        if structural_only:
            matched_aliases = []
        if not evidence and not matched_aliases:
            continue
        detected.append({
            "id": standard["id"],
            "name": standard["name"],
            "full_name": standard["full_name"],
            "matched_aliases": matched_aliases,
            "evidence": evidence,
            "priority": HARDWARE_STANDARDS_PRIORITY,
            "domains": standard["domains"],
            "attributes": standard["attributes"],
            "source": {
                "name": standard["source"]["name"],
                "url": standard["source"]["url"],
                "status": standard["source"]["status"],
                "version": standard["source"].get("version"),
            },
        })

    return {
        "pack_id": "hardware-standards-p0",
        "priority": HARDWARE_STANDARDS_PRIORITY,
        "detected_standards": detected,
        "detected_ids": [item["id"] for item in detected],
        "physical_hardware_focus": sorted({
            domain
            for item in detected
            for domain in item["domains"]
            if domain not in {
                "firmware", "boot", "configuration", "security", "bios",
                "platform initialization", "boot architecture",
            }
        }),
        "firmware_focus": sorted({
            domain
            for item in detected
            for domain in item["domains"]
            if domain in {
                "firmware", "boot", "configuration", "security", "bios",
                "platform initialization", "boot architecture",
            }
        }),
    }


def _detect_structural_signatures(
    dataframe: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    headers = {
        re.sub(r"[\s\-_/]+", "_", _normalize(column))
        for column in dataframe.columns
    }
    columns_by_name = {
        re.sub(r"[\s\-_/]+", "_", _normalize(column)): str(column)
        for column in dataframe.columns
    }
    values_by_header: dict[str, list[str]] = {}
    raw_values_by_header: dict[str, list[str]] = {}
    for normalized, column in columns_by_name.items():
        raw_values = [
            str(value)
            for value in dataframe[column].head(100).tolist()
            if value is not None and not pd.isna(value) and str(value).strip()
        ]
        raw_values_by_header[normalized] = raw_values
        values_by_header[normalized] = [
            _normalize(value)
            for value in raw_values
        ]
    all_values = {
        value
        for values in values_by_header.values()
        for value in values
    }

    evidence: dict[str, list[dict[str, Any]]] = {}

    def add(
        standard_id: str,
        required_fields: set[str],
        *,
        matched_values: set[str] | None = None,
    ) -> None:
        matched_fields = sorted(headers & required_fields)
        evidence.setdefault(standard_id, []).append({
            "basis": "structural_signature",
            "matched_fields": matched_fields,
            "matched_values": sorted(matched_values or set()),
        })

    smbios_fields = {"smbios_structure_type", "structure_name", "structure_type"}
    smbios_types = set(values_by_header.get("smbios_structure_type", []))
    if len(headers & smbios_fields) >= 2 or (
        "smbios_structure_type" in headers
        and "handle" in headers
    ):
        add(
            "SMBIOS",
            smbios_fields | {"handle"},
            matched_values=smbios_types,
        )

    if "smbios_structure_type" in headers and "handle" in headers:
        add(
            "DMI",
            {"smbios_structure_type", "handle", "structure_name"},
            matched_values=smbios_types,
        )

    uefi_fields = {
        "variable_name", "vendor_guid", "value_raw", "attributes",
        "boot_related",
    }
    uefi_values = {
        value for value in all_values
        if value in {"bootorder", "secureboot", "setupmode", "firmwarevendor"}
    }
    if len(headers & uefi_fields) >= 2 or (
        "variable_name" in headers and len(uefi_values) >= 2
    ):
        add("UEFI", uefi_fields, matched_values=uefi_values)

    acpi_fields = {
        "acpi_object_path", "object_type", "hardware_id", "compatible_ids",
        "resource_type", "power_state",
    }
    acpi_paths = {
        value for value in raw_values_by_header.get("acpi_object_path", [])
        if re.search(r"(?:^|\\)_SB(?:\.|$)|(?:^|\\)_PR(?:\.|$)", value, re.I)
    }
    if (
        "acpi_object_path" in headers
        or (
            "hardware_id" in headers
            and bool(headers & {"object_type", "compatible_ids"})
        )
        or acpi_paths
    ):
        add("ACPI", acpi_fields, matched_values=acpi_paths)

    cim_fields = {
        "cim_namespace", "cim_class", "property_name", "property_value",
        "data_type",
    }
    if len(headers & cim_fields) >= 3:
        add("CIM", cim_fields)

    redfish_fields = {
        "resource_uri", "resource_type", "member_id", "reading_type",
        "reading", "reading_units", "status_health", "status_state",
    }
    if "resource_uri" in headers and len(headers & redfish_fields) >= 3:
        add("REDFISH", redfish_fields)

    ipmi_fields = {
        "sensor_number", "sensor_type", "reading_value", "lower_critical",
        "upper_critical", "event_status", "sensor_owner",
    }
    if (
        "sensor_number" in headers
        and "sensor_type" in headers
        and len(headers & ipmi_fields) >= 4
    ):
        add("IPMI", ipmi_fields)

    phase_column = columns_by_name.get("phase")
    phases = set()
    if phase_column:
        phases = {
            _normalize(value).upper()
            for value in dataframe[phase_column].head(200).tolist()
            if value is not None and not pd.isna(value)
        } & {"SEC", "PEI", "DXE", "BDS", "RT"}
    if len(phases) >= 2:
        add("UEFI_PI", {"phase", "component", "responsibility"}, matched_values=phases)

    return evidence
