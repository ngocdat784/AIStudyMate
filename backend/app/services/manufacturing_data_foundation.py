from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.services.multi_file_service import (
    CANONICAL_COLUMN_ALIASES,
    _parse_numeric,
)


_ADDITIONAL_ALIASES: dict[str, tuple[str, ...]] = {
    "Production": (
        *CANONICAL_COLUMN_ALIASES["Production"],
        "actual_production",
        "actual_output",
        "actual_quantity",
        "good_units",
        "actual_units",
    ),
    "Line": ("line", "line_id", "line_name", "work_center", "chuyền", "day_chuyen"),
    "Shift": ("shift", "shift_id", "ca", "ca_lam_viec"),
    "Machine": ("machine", "machine_id", "machine_name", "máy", "ma_may"),
    "Operator": (
        "operator",
        "operator_id",
        "operator_name",
        "employee",
        "employee_id",
        "technician",
    ),
    "Process": ("process", "process_name", "operation", "công đoạn", "cong_doan"),
    "Method": (
        "method",
        "work_method",
        "work_instruction",
        "procedure",
        "process_method",
    ),
    "Environment": (
        "environment",
        "environmental_condition",
        "ambient_condition",
        "working_condition",
    ),
    "Material": ("material", "material_id", "material_code", "nguyên liệu", "nguyen_lieu"),
    "Order": ("order", "order_id", "work_order", "production_order"),
    "Defect": ("defect", "defect_description", "defect_name", "mô tả lỗi"),
    "Planned Production": (
        "planned_production",
        "planned_output",
        "planned_quantity",
        "planned_qty",
        "planned_units",
        "plan_qty",
        "plan",
        "production_plan",
        "target_production",
        "target_quantity",
        "kế hoạch sản lượng",
        "san_luong_ke_hoach",
    ),
    "Production Start": (
        "production_start",
        "start_time",
        "start_datetime",
        "shift_start",
        "thời gian bắt đầu",
    ),
    "Production End": (
        "production_end",
        "end_time",
        "end_datetime",
        "shift_end",
        "thời gian kết thúc",
    ),
    "Cycle Time": (
        "cycle_time",
        "actual_cycle_time",
        "chu_ky_san_xuat",
        "thời gian chu kỳ",
    ),
    "Standard Cycle Time": (
        "standard_cycle_time",
        "ideal_cycle_time",
        "chu_ky_chuan",
        "thời gian chu kỳ chuẩn",
    ),
    "Available Time": (
        "available_time",
        "available_time_minutes",
        "available_time_seconds",
        "scheduled_time",
        "planned_time",
        "thời gian khả dụng",
    ),
    "Operating Time": (
        "operating_time",
        "operating_time_minutes",
        "operating_time_seconds",
        "run_time",
        "running_time",
        "thời gian chạy máy",
    ),
    "Waiting Time": (
        "waiting_time",
        "waiting_time_minutes",
        "waiting_time_seconds",
        "queue_time",
        "thời gian chờ",
    ),
    "Inspected Quantity": (
        "inspected_quantity",
        "total_inspected",
        "inspected",
        "inspected_qty",
        "inspected_units",
        "quantity_inspected",
        "sample_size",
    ),
    "Defective Units": (
        "defective_units",
        "defective_quantity",
        "failed_units",
        "rejected_units",
        "nonconforming_units",
        "bad_units",
    ),
    "First Pass Inspected": (
        "first_pass_inspected",
        "first_time_inspected",
        "initial_inspected",
    ),
    "First Pass Passed": (
        "first_pass_passed",
        "first_pass_good_units",
        "accepted_first_pass",
        "passed_first_time",
    ),
    "Reworked Units": (
        "reworked_units",
        "reworked",
        "rework_units",
        "rework_qty",
        "reworked_count",
    ),
    "Scrapped Units": (
        "scrapped_units",
        "scrapped",
        "scrap_units",
        "scrap_qty",
        "scrapped_count",
    ),
    "Process Sequence": (
        "process_sequence",
        "process_order",
        "operation_sequence",
        "step_number",
        "process_step",
    ),
    "Subgroup ID": (
        "subgroup_id",
        "subgroup",
        "sample_group",
        "rational_subgroup",
    ),
    "Measurement Characteristic": (
        "measurement_characteristic",
        "measured_characteristic",
        "measurement_name",
        "characteristic",
        "quality_characteristic",
    ),
    "Measurement Value": (
        "measurement_value",
        "measured_value",
        "observed_value",
        "reading",
    ),
    "Lower Spec Limit": (
        "lower_spec_limit",
        "lower_specification_limit",
        "lsl",
        "minimum_specification",
    ),
    "Upper Spec Limit": (
        "upper_spec_limit",
        "upper_specification_limit",
        "usl",
        "maximum_specification",
    ),
}
_CANONICAL_ALIASES = {
    **CANONICAL_COLUMN_ALIASES,
    **_ADDITIONAL_ALIASES,
}
_DOWNTIME_TO_MINUTES = {
    "seconds": 1 / 60,
    "minutes": 1.0,
    "hours": 60.0,
}
_TIME_TO_SECONDS = {
    "seconds": 1.0,
    "minutes": 60.0,
    "hours": 3600.0,
}
_DURATION_TO_MINUTES = {
    "seconds": 1 / 60,
    "minutes": 1.0,
    "hours": 60.0,
}
_CANONICAL_TARGETS = {
    re.sub(r"[\W]+", "_", target.casefold(), flags=re.UNICODE).strip("_"): target
    for target in _CANONICAL_ALIASES
}
_CANONICAL_TARGETS["timestamp"] = "Date"
_CANONICAL_TARGETS["production_timestamp"] = "Date"
_ALIAS_TO_TARGET = {
    re.sub(r"[\W]+", "_", alias.casefold(), flags=re.UNICODE).strip("_"): target
    for target, aliases in _CANONICAL_ALIASES.items()
    for alias in aliases
}
_ALIAS_TO_TARGET.update(_CANONICAL_TARGETS)


class ManufacturingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime | None = None
    product: str | None = None
    model: str | None = None
    component: str | None = None
    supplier: str | None = None
    line: str | None = None
    shift: str | None = None
    machine: str | None = None
    operator: str | None = None
    process: str | None = None
    method: str | None = None
    environment: str | None = None
    material: str | None = None
    batch: str | None = None
    defect: str | None = None
    defect_type: str | None = None
    quality_status: str | None = None
    order: str | None = None
    process_sequence: int | None = Field(default=None, ge=1)
    subgroup_id: str | None = None
    measurement_name: str | None = None
    measurement_value: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    lower_spec_limit: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    upper_spec_limit: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    production: float = Field(ge=0, allow_inf_nan=False)
    planned_production: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    production_start: datetime | None = None
    production_end: datetime | None = None
    cycle_time_seconds: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    standard_cycle_time_seconds: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    available_time_minutes: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    operating_time_minutes: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    waiting_time_minutes: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    production_unit: str
    defects: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    inspected_quantity: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    defective_units: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    first_pass_inspected: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    first_pass_passed: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    reworked_units: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    scrapped_units: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    downtime_minutes: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_row: int = Field(ge=2)


class ManufacturingDataQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_score: float = Field(ge=0, le=100)
    quality_status: str
    rows_read: int = Field(ge=0)
    empty_rows: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    invalid_rows: int = Field(ge=0)
    rejected_rows: int = Field(ge=0)
    rows_used_for_analysis: int = Field(ge=0)
    missing_values: dict[str, int]
    invalid_values: dict[str, int]
    warnings: list[str]
    scoring_basis: str


class ManufacturingDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    records: list[ManufacturingRecord]
    source_timezone: str
    stored_timezone: str = "UTC"
    units: dict[str, str]
    column_mapping: dict[str, str]
    data_quality: ManufacturingDataQuality


def _normalize_header(value: Any) -> str:
    return re.sub(
        r"[\W]+",
        "_",
        str(value).strip().casefold(),
        flags=re.UNICODE,
    ).strip("_")


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if getattr(missing, "shape", ()) not in ((), None):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _json_safe(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _resolve_mapping(
    columns: list[Any],
    column_mapping: dict[str, str] | None,
) -> dict[str, str]:
    if len(set(map(str, columns))) != len(columns):
        raise ValueError("File chứa tên cột trùng lặp.")

    if column_mapping is not None:
        mapping: dict[str, str] = {}
        used_targets: set[str] = set()
        for source, requested_target in column_mapping.items():
            if source not in columns:
                raise ValueError(f"Không tìm thấy cột nguồn '{source}'.")
            target_key = _normalize_header(requested_target)
            target = _ALIAS_TO_TARGET.get(target_key)
            if target is None:
                if requested_target.startswith("attributes."):
                    target = requested_target
                else:
                    raise ValueError(
                        f"Trường chuẩn hóa không được hỗ trợ: {requested_target}."
                    )
            if target in used_targets:
                raise ValueError(f"Nhiều cột nguồn cùng ánh xạ tới '{target}'.")
            mapping[source] = target
            used_targets.add(target)
        return mapping

    aliases = {
        _normalize_header(alias): target
        for target, target_aliases in _CANONICAL_ALIASES.items()
        for alias in (target, *target_aliases)
    }
    mapping = {}
    used_targets = set()
    for column in columns:
        target = aliases.get(_normalize_header(column))
        if target is not None and target not in used_targets:
            mapping[str(column)] = target
            used_targets.add(target)
    return mapping


def _parse_timestamp(value: Any, source_timezone: ZoneInfo) -> datetime:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("invalid_timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(
            source_timezone,
            ambiguous="raise",
            nonexistent="raise",
        )
    return timestamp.tz_convert("UTC").to_pydatetime()


def normalize_manufacturing_dataframe(
    dataframe: pd.DataFrame,
    *,
    column_mapping: dict[str, str] | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    require_production: bool = True,
) -> ManufacturingDataset:
    if dataframe.empty:
        raise ValueError("File không chứa bản ghi dữ liệu.")
    if downtime_unit not in _DOWNTIME_TO_MINUTES:
        raise ValueError("Downtime unit phải là seconds, minutes hoặc hours.")
    if cycle_time_unit not in _TIME_TO_SECONDS:
        raise ValueError("Cycle time unit phải là seconds, minutes hoặc hours.")
    if duration_unit not in _DURATION_TO_MINUTES:
        raise ValueError("Duration unit phải là seconds, minutes hoặc hours.")
    normalized_production_unit = production_unit.strip().casefold()
    if not normalized_production_unit or len(normalized_production_unit) > 40:
        raise ValueError("Production unit không hợp lệ.")
    try:
        timezone_info = ZoneInfo(source_timezone)
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise ValueError("Múi giờ nguồn không hợp lệ.") from error

    mapping = _resolve_mapping(list(dataframe.columns), column_mapping)
    target_to_source = {target: source for source, target in mapping.items()}
    if "Production" not in target_to_source and require_production:
        raise ValueError("Cần ánh xạ cột Production.")

    mapped_sources = set(mapping)
    attributes_sources = {
        str(column)
        for column in dataframe.columns
        if str(column) not in mapped_sources
    }
    missing_values = {
        target: 0
        for target in target_to_source.values()
        if not target.startswith("attributes.")
    }
    invalid_values: dict[str, int] = {}
    records: list[ManufacturingRecord] = []
    seen_records: set[str] = set()
    warnings: list[str] = []
    empty_rows = duplicate_rows = invalid_rows = rejected_rows = 0
    rows_read = len(dataframe.index)
    downtime_factor = _DOWNTIME_TO_MINUTES[downtime_unit]

    for source_row_number, source_row in enumerate(
        dataframe.itertuples(index=False, name=None),
        start=2,
    ):
        row = dict(zip(dataframe.columns, source_row))
        if all(_is_missing(value) or not str(value).strip() for value in row.values()):
            empty_rows += 1
            continue

        row_issues = 0
        values: dict[str, Any] = {}
        attributes = {
            re.sub(r"[^A-Za-z0-9_]+", "_", source): _json_safe(row[source])
            for source in attributes_sources
            if not _is_missing(row[source])
        }

        production_source = target_to_source.get("Production")
        if production_source is None:
            values["production"] = 0.0
        else:
            production_value, production_issue = _parse_numeric(
                row[production_source]
            )
            if production_value is None or production_issue is not None:
                invalid_rows += 1
                invalid_values["Production"] = (
                    invalid_values.get("Production", 0) + 1
                )
                if require_production:
                    rejected_rows += 1
                    if len(warnings) < 20:
                        warnings.append(
                            f"Dòng {source_row_number}: Production thiếu hoặc không hợp lệ."
                        )
                    continue
                values["production"] = 0.0
                row_issues = 1
            else:
                values["production"] = float(production_value)

        for target, field_name in (
            ("Defects", "defects"),
            ("Downtime", "downtime_minutes"),
            ("Planned Production", "planned_production"),
            ("Cycle Time", "cycle_time_seconds"),
            ("Standard Cycle Time", "standard_cycle_time_seconds"),
            ("Available Time", "available_time_minutes"),
            ("Operating Time", "operating_time_minutes"),
            ("Waiting Time", "waiting_time_minutes"),
            ("Inspected Quantity", "inspected_quantity"),
            ("Defective Units", "defective_units"),
            ("First Pass Inspected", "first_pass_inspected"),
            ("First Pass Passed", "first_pass_passed"),
            ("Reworked Units", "reworked_units"),
            ("Scrapped Units", "scrapped_units"),
            ("Measurement Value", "measurement_value"),
            ("Lower Spec Limit", "lower_spec_limit"),
            ("Upper Spec Limit", "upper_spec_limit"),
            ("Process Sequence", "process_sequence"),
        ):
            source = target_to_source.get(target)
            if source is None or _is_missing(row[source]):
                values[field_name] = None
                if source is not None:
                    missing_values[target] = missing_values.get(target, 0) + 1
                continue
            number, issue = _parse_numeric(row[source])
            if issue == "negative" and target in {
                "Measurement Value",
                "Lower Spec Limit",
                "Upper Spec Limit",
            }:
                issue = None
            if number is None or issue is not None:
                values[field_name] = None
                row_issues += 1
                invalid_values[target] = invalid_values.get(target, 0) + 1
                continue
            if target == "Process Sequence":
                if number < 1 or not float(number).is_integer():
                    values[field_name] = None
                    row_issues += 1
                    invalid_values[target] = invalid_values.get(target, 0) + 1
                    continue
                values[field_name] = int(number)
                continue
            if target in {
                "Defects",
                "Downtime",
                "Planned Production",
                "Cycle Time",
                "Standard Cycle Time",
                "Available Time",
                "Operating Time",
                "Waiting Time",
                "Inspected Quantity",
                "Defective Units",
                "First Pass Inspected",
                "First Pass Passed",
                "Reworked Units",
                "Scrapped Units",
            } and number < 0:
                values[field_name] = None
                row_issues += 1
                invalid_values[target] = invalid_values.get(target, 0) + 1
                continue
            values[field_name] = (
                float(number) * downtime_factor
                if target == "Downtime"
                else float(number) * _TIME_TO_SECONDS[cycle_time_unit]
                if target in {"Cycle Time", "Standard Cycle Time"}
                else float(number) * _DURATION_TO_MINUTES[duration_unit]
                if target
                in {"Available Time", "Operating Time", "Waiting Time"}
                else float(number)
            )

        timestamp_source = target_to_source.get("Date")
        timestamp = None
        if timestamp_source is not None:
            if _is_missing(row[timestamp_source]):
                missing_values["Date"] = missing_values.get("Date", 0) + 1
                row_issues += 1
            else:
                try:
                    timestamp = _parse_timestamp(
                        row[timestamp_source],
                        timezone_info,
                    )
                except (TypeError, ValueError, OverflowError):
                    invalid_values["Date"] = invalid_values.get("Date", 0) + 1
                    row_issues += 1

        interval_timestamps: dict[str, datetime | None] = {}
        for target, field_name in (
            ("Production Start", "production_start"),
            ("Production End", "production_end"),
        ):
            source = target_to_source.get(target)
            parsed_timestamp = None
            if source is not None:
                if _is_missing(row[source]):
                    missing_values[target] = missing_values.get(target, 0) + 1
                    row_issues += 1
                else:
                    try:
                        parsed_timestamp = _parse_timestamp(
                            row[source],
                            timezone_info,
                        )
                    except (TypeError, ValueError, OverflowError):
                        invalid_values[target] = invalid_values.get(target, 0) + 1
                        row_issues += 1
            interval_timestamps[field_name] = parsed_timestamp
            values[field_name] = parsed_timestamp

        canonical_field_names = {
            "Product": "product",
            "Model": "model",
            "Component": "component",
            "Supplier": "supplier",
            "Line": "line",
            "Shift": "shift",
            "Machine": "machine",
            "Operator": "operator",
            "Process": "process",
            "Method": "method",
            "Environment": "environment",
            "Material": "material",
            "Batch": "batch",
            "Defect": "defect",
            "Defect Type": "defect_type",
            "Quality Status": "quality_status",
            "Order": "order",
            "Subgroup ID": "subgroup_id",
            "Measurement Characteristic": "measurement_name",
            "Planned Production": "planned_production",
            "Cycle Time": "cycle_time_seconds",
            "Standard Cycle Time": "standard_cycle_time_seconds",
            "Available Time": "available_time_minutes",
            "Operating Time": "operating_time_minutes",
            "Waiting Time": "waiting_time_minutes",
            "Inspected Quantity": "inspected_quantity",
            "Defective Units": "defective_units",
            "First Pass Inspected": "first_pass_inspected",
            "First Pass Passed": "first_pass_passed",
            "Reworked Units": "reworked_units",
            "Scrapped Units": "scrapped_units",
            "Process Sequence": "process_sequence",
            "Measurement Value": "measurement_value",
            "Lower Spec Limit": "lower_spec_limit",
            "Upper Spec Limit": "upper_spec_limit",
        }
        for target, field_name in canonical_field_names.items():
            source = target_to_source.get(target)
            if field_name in values:
                continue
            raw_value = row[source] if source is not None else None
            if source is not None and _is_missing(raw_value):
                missing_values[target] = missing_values.get(target, 0) + 1
            values[field_name] = (
                str(raw_value).strip()
                if source is not None and not _is_missing(raw_value)
                else None
            )

        for target, source in target_to_source.items():
            if target.startswith("attributes.") and not _is_missing(row[source]):
                attributes[target.removeprefix("attributes.")] = _json_safe(
                    row[source]
                )

        for target, numerator_field, denominator_field in (
            ("Defective Units", "defective_units", "inspected_quantity"),
            (
                "First Pass Passed",
                "first_pass_passed",
                "first_pass_inspected",
            ),
            (
                "First Pass Inspected",
                "first_pass_inspected",
                "inspected_quantity",
            ),
        ):
            numerator = values.get(numerator_field)
            denominator = values.get(denominator_field)
            if (
                numerator is not None
                and denominator is not None
                and numerator > denominator
            ):
                values[numerator_field] = None
                row_issues += 1
                invalid_values[target] = invalid_values.get(target, 0) + 1

        record = ManufacturingRecord(
            timestamp=timestamp,
            production=values["production"],
            production_unit=normalized_production_unit,
            defects=values.get("defects"),
            downtime_minutes=values.get("downtime_minutes"),
            production_start=interval_timestamps.get("production_start"),
            production_end=interval_timestamps.get("production_end"),
            attributes=attributes,
            source_row=source_row_number,
            **{
                field: value
                for field, value in values.items()
                if field in canonical_field_names.values()
            },
        )
        identity = record.model_dump_json(exclude={"source_row"})
        if identity in seen_records:
            duplicate_rows += 1
            invalid_rows += 1
            continue
        seen_records.add(identity)
        records.append(record)
        if row_issues:
            invalid_rows += 1

    if not records:
        raise ValueError("Không còn bản ghi có Production hợp lệ để phân tích.")

    non_empty_rows = rows_read - empty_rows
    valid_rows = max(0, non_empty_rows - invalid_rows)
    quality_score = (
        round(valid_rows / non_empty_rows * 100, 1)
        if non_empty_rows
        else 0.0
    )
    quality_status = (
        "good"
        if quality_score >= 95
        else "warning"
        if quality_score >= 80
        else "poor"
    )
    if timestamp_source := target_to_source.get("Date"):
        if all(record.timestamp is None for record in records):
            warnings.append(
                f"Cột {timestamp_source} không có timestamp hợp lệ; không thể phân tích trend."
            )
    else:
        warnings.append(
            "Không có cột timestamp/date; không thể phân tích trend."
        )
    if duplicate_rows:
        warnings.append(
            f"Đã bỏ {duplicate_rows} dòng trùng lặp sau lần xuất hiện đầu tiên."
        )
    if rejected_rows:
        warnings.append(
            f"Đã loại {rejected_rows} dòng thiếu hoặc sai Production."
        )

    quality = ManufacturingDataQuality(
        quality_score=quality_score,
        quality_status=quality_status,
        rows_read=rows_read,
        empty_rows=empty_rows,
        duplicate_rows=duplicate_rows,
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        rejected_rows=rejected_rows,
        rows_used_for_analysis=len(records),
        missing_values=missing_values,
        invalid_values=invalid_values,
        warnings=warnings,
        scoring_basis=(
            "valid unique non-empty rows / non-empty rows * 100; "
            "optional fields missing from the source schema are not penalized; "
            "invalid populated values and duplicate occurrences are penalized."
        ),
    )

    return ManufacturingDataset(
        records=records,
        source_timezone=source_timezone,
        units={
            "production": normalized_production_unit,
            "defects": "count",
            "downtime": "minutes",
            "defect_rate": "percent",
            "cycle_time": "seconds",
            "duration": "minutes",
        },
        column_mapping=mapping,
        data_quality=quality,
    )
