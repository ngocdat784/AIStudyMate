from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


_KNOWLEDGE_BASE_ROOT_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "knowledge_base",
    Path(__file__).resolve().parents[2] / "knowledge_base",
)


def _knowledge_base_root() -> Path:
    for candidate in _KNOWLEDGE_BASE_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate.resolve()
    return _KNOWLEDGE_BASE_ROOT_CANDIDATES[0].resolve()


@lru_cache(maxsize=None)
def load_knowledge_json(relative_path: str) -> dict[str, Any]:
    root = _knowledge_base_root()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise ValueError("Knowledge base path must remain inside knowledge_base.")
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Could not load HardwareAI knowledge file: {relative_path}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError(f"Knowledge file must contain a JSON object: {relative_path}")
    return data


def _normalize_header(value: Any) -> str:
    return re.sub(
        r"[\W]+",
        "_",
        str(value).strip().casefold(),
        flags=re.UNICODE,
    ).strip("_")


def get_knowledge_column_aliases() -> dict[str, tuple[str, ...]]:
    data = load_knowledge_json("dictionaries/column_aliases.json")
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Knowledge column aliases must define a fields object.")
    aliases: dict[str, tuple[str, ...]] = {}
    for target, values in fields.items():
        if not isinstance(target, str) or not target:
            raise ValueError(f"Unsupported knowledge column target: {target!r}")
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise ValueError(f"Aliases must be a list of strings: {target}")
        aliases[target] = tuple(values)
    return aliases


def _taxonomy_categories() -> list[dict[str, Any]]:
    data = load_knowledge_json("taxonomies/hardware_categories.json")
    categories = data.get("categories")
    if not isinstance(categories, list):
        raise ValueError("Hardware taxonomy must contain a categories list.")
    return categories


def _match_sensors(column: str) -> list[dict[str, str]]:
    data = load_knowledge_json("dictionaries/sensor_dictionary.json")
    sensors = data.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError("Sensor dictionary must contain a sensors list.")
    normalized = _normalize_header(column).replace("_", " ")
    matches = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        aliases = sensor.get("aliases", [])
        if any(
            re.search(
                rf"(?<!\w){re.escape(_normalize_header(alias).replace('_', ' '))}(?!\w)",
                normalized,
            )
            for alias in aliases
        ):
            matches.append({
                "id": str(sensor["id"]),
                "unit": str(sensor["unit"]),
                "category": str(sensor["category"]),
            })
    return matches


def _infer_unknown_field(
    source: str,
    values: pd.Series,
    category: str | None,
) -> dict[str, Any]:
    samples = values.dropna().loc[lambda series: series.astype(str).str.strip().ne("")].head(100)
    sample_values = [value for value in samples.tolist()]
    normalized_name = _normalize_header(source).replace("_", " ")
    if not sample_values:
        inferred_type = "unknown"
    else:
        parsed_json = []
        for value in sample_values:
            if not isinstance(value, str):
                parsed_json.append(None)
                continue
            try:
                parsed_json.append(json.loads(value))
            except json.JSONDecodeError:
                parsed_json.append(None)
        if all(isinstance(value, (dict, list)) for value in parsed_json):
            inferred_type = "json"
        elif all(
            isinstance(value, (bool,)) or str(value).strip().casefold() in {
                "true", "false", "yes", "no", "on", "off",
            }
            for value in sample_values
        ):
            inferred_type = "boolean"
        elif (
            re.search(r"\b(date|time|timestamp|collected at)\b", normalized_name)
            and pd.to_datetime(
                pd.Series(sample_values),
                format="mixed",
                errors="coerce",
            ).notna().all()
        ):
            inferred_type = "datetime"
        else:
            numeric_values = pd.to_numeric(
                pd.Series(sample_values),
                errors="coerce",
            )
            if numeric_values.notna().all():
                inferred_type = (
                    "integer"
                    if (numeric_values % 1 == 0).all()
                    else "number"
                )
            else:
                inferred_type = "string"

    if inferred_type == "json" and "payload" in normalized_name:
        inferred_meaning = "Structured payload; schema and semantics are unknown."
    elif inferred_type in {"integer", "number"} and any(
        word in normalized_name for word in ("metric", "reading", "value")
    ):
        inferred_meaning = "Numeric measurement; semantic meaning is unconfirmed."
    elif any(word in normalized_name for word in ("id", "identifier", "code")):
        inferred_meaning = "Identifier; namespace and semantics are unconfirmed."
    else:
        inferred_meaning = "Meaning cannot be inferred reliably from available data."

    return {
        "source_column": source,
        "inferred_category": category,
        "inferred_type": inferred_type,
        "inferred_meaning": inferred_meaning,
    }


def profile_hardware_dataframe(dataframe: pd.DataFrame) -> dict[str, Any]:
    aliases = get_knowledge_column_aliases()
    alias_to_target: dict[str, str] = {}
    for target, names in aliases.items():
        for alias in (target, target.removeprefix("attributes."), *names):
            normalized = _normalize_header(alias)
            existing = alias_to_target.get(normalized)
            if existing is not None and existing != target:
                raise ValueError(
                    f"Knowledge alias {alias!r} maps to multiple attributes."
                )
            alias_to_target[normalized] = target

    column_mapping: dict[str, str] = {}
    unknown_fields: list[dict[str, Any]] = []
    mapped_categories: set[str] = set()
    unit_mappings: list[dict[str, str]] = []
    recognized_sensors: list[dict[str, str]] = []
    for column in dataframe.columns:
        source = str(column)
        target = alias_to_target.get(_normalize_header(source))
        if target is not None:
            column_mapping[source] = target
            if target.startswith("attributes."):
                attribute_id = target.removeprefix("attributes.")
                dictionary = load_knowledge_json(
                    "dictionaries/attribute_dictionary.json"
                )
                for attribute in dictionary.get("attributes", []):
                    if attribute.get("id") == attribute_id:
                        mapped_categories.add(str(attribute["category"]))
                        break
            continue

        normalized = _normalize_header(source).replace("_", " ")
        sensor_matches = _match_sensors(source)
        if sensor_matches:
            for sensor in sensor_matches:
                mapped_categories.add(sensor["category"])
                recognized_sensors.append({
                    "source_column": source,
                    "sensor_id": sensor["id"],
                    "unit": sensor["unit"],
                })
        category = next(
            (
                str(item["id"])
                for item in _taxonomy_categories()
                if any(
                    _normalize_header(keyword).replace("_", " ") in normalized
                    for keyword in item["keywords"]
                )
            ),
            None,
        )
        if not sensor_matches:
            unknown_fields.append(
                _infer_unknown_field(source, dataframe[column], category)
            )
        if category is not None:
            mapped_categories.add(category)

        header_unit = re.search(r"[\[(]([^\])]+)[\])]\s*$", source)
        if header_unit:
            normalized_unit = normalize_unit_label(header_unit.group(1))
            if normalized_unit is not None and normalized_unit != header_unit.group(1):
                unit_mappings.append({
                    "source_column": source,
                    "source_unit": header_unit.group(1),
                    "normalized_unit": normalized_unit,
                })
        elif re.search(r"(?:^|[_\s])(?:unit|units|uom)(?:$|[_\s])", source, re.I):
            observed_units = (
                dataframe[column]
                .dropna()
                .astype(str)
                .str.strip()
                .loc[lambda values: values.ne("")]
                .unique()[:20]
            )
            for observed_unit in observed_units:
                normalized_unit = normalize_unit_label(observed_unit)
                if normalized_unit is not None and normalized_unit != observed_unit:
                    unit_mappings.append({
                        "source_column": source,
                        "source_unit": observed_unit,
                        "normalized_unit": normalized_unit,
                    })

    return {
        "schema_version": "1.0.0",
        "raw_columns": [str(column) for column in dataframe.columns],
        "column_mapping": column_mapping,
        "unknown_fields": unknown_fields,
        "hardware_categories": sorted(mapped_categories),
        "recognized_sensors": recognized_sensors,
        "unit_mappings": unit_mappings,
        "row_count": len(dataframe.index),
        "raw_values_preserved": True,
    }


def normalize_unit_label(unit: str | None) -> str | None:
    if unit is None:
        return None
    value = unit.strip()
    if not value:
        return None
    data = load_knowledge_json("dictionaries/unit_dictionary.json")
    normalization = data.get("normalization")
    if not isinstance(normalization, dict):
        raise ValueError("Unit dictionary must define a normalization object.")
    normalized = value.casefold()
    matches = {
        canonical
        for unit_aliases in normalization.values()
        if isinstance(unit_aliases, dict)
        for canonical, aliases in unit_aliases.items()
        if normalized == canonical.casefold()
        or (
            isinstance(aliases, list)
            and normalized in {str(alias).casefold() for alias in aliases}
        )
    }
    if len(matches) > 1:
        raise ValueError(f"Ambiguous hardware unit alias: {unit}")
    return next(iter(matches)) if matches else value
