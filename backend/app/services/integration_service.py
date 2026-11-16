from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from app.services.multi_file_service import _parse_numeric


MAX_INTEGRATION_ROWS = 100_000
MAX_VALIDATION_SAMPLES = 20
CANONICAL_FIELDS = (
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
    "Machine",
    "Process",
    "Order",
    "Material",
)
REQUIRED_FIELDS = ("Date", "Production")
NUMERIC_FIELDS = {"Production", "Defects", "Downtime"}
CODE_FIELDS = {"Product", "Model", "Component", "Supplier", "Batch", "Machine"}
_ATTRIBUTE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_DOWNTIME_TO_MINUTES = {
    "minutes": 1.0,
    "seconds": 1.0 / 60.0,
    "hours": 60.0,
}


class IntegrationValidationError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def validate_connector_mapping(
    mapping: dict[str, str],
    normalization: dict[str, Any],
) -> dict[str, Any]:
    if not mapping:
        raise ValueError("Hãy ánh xạ ít nhất các cột bắt buộc.")
    if len(mapping) > 100:
        raise ValueError("Chỉ hỗ trợ ánh xạ tối đa 100 cột.")

    targets: set[str] = set()
    for source, target in mapping.items():
        if not source.strip() or len(source) > 255:
            raise ValueError("Tên cột nguồn không hợp lệ.")
        if target in CANONICAL_FIELDS:
            if target in targets:
                raise ValueError(f"Nhiều cột nguồn cùng ánh xạ tới {target}.")
            targets.add(target)
        elif target.startswith("attributes."):
            attribute_name = target.removeprefix("attributes.")
            if not _ATTRIBUTE_NAME.fullmatch(attribute_name):
                raise ValueError(
                    f"Tên thuộc tính mở rộng không hợp lệ: {attribute_name}."
                )
            if target in targets:
                raise ValueError(
                    f"Nhiều cột nguồn cùng ánh xạ tới {target}."
                )
            targets.add(target)
        else:
            raise ValueError(f"Trường đích không được hỗ trợ: {target}.")

    missing = set(REQUIRED_FIELDS) - targets
    if missing:
        raise ValueError(
            "Cần ánh xạ đủ các trường bắt buộc: "
            + ", ".join(sorted(missing))
            + "."
        )

    date_timezone = normalization.get("date_timezone", "UTC")
    try:
        ZoneInfo(date_timezone)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("Múi giờ dữ liệu không hợp lệ.") from None

    downtime_unit = normalization.get("downtime_unit", "minutes")
    if downtime_unit not in _DOWNTIME_TO_MINUTES:
        raise ValueError(
            "Đơn vị Downtime phải là minutes, seconds hoặc hours."
        )

    uppercase_fields = normalization.get("uppercase_code_fields", [])
    if (
        not isinstance(uppercase_fields, list)
        or any(field not in CODE_FIELDS for field in uppercase_fields)
    ):
        raise ValueError("Danh sách trường chuẩn hóa mã không hợp lệ.")

    return {
        "date_timezone": date_timezone,
        "downtime_unit": downtime_unit,
        "uppercase_code_fields": uppercase_fields,
    }


def suggest_column_mapping(columns: list[str]) -> dict[str, str]:
    aliases = {
        "date": "Date",
        "production_date": "Date",
        "work_date": "Date",
        "ngày": "Date",
        "ngay": "Date",
        "product": "Product",
        "product_name": "Product",
        "product_code": "Product",
        "sản phẩm": "Product",
        "san_pham": "Product",
        "model": "Model",
        "component": "Component",
        "supplier": "Supplier",
        "vendor": "Supplier",
        "batch": "Batch",
        "lot": "Batch",
        "production": "Production",
        "production_qty": "Production",
        "output": "Production",
        "quantity": "Production",
        "sản lượng": "Production",
        "san_luong": "Production",
        "defects": "Defects",
        "defect_count": "Defects",
        "số lỗi": "Defects",
        "so_loi": "Defects",
        "downtime": "Downtime",
        "downtime_minutes": "Downtime",
        "defect_type": "Defect Type",
        "quality_status": "Quality Status",
        "machine": "Machine",
        "machine_id": "Machine",
        "machine_code": "Machine",
        "process": "Process",
        "order": "Order",
        "order_id": "Order",
        "material": "Material",
        "material_code": "Material",
    }
    mapping: dict[str, str] = {}
    used_targets: set[str] = set()
    for column in columns:
        normalized = re.sub(r"[\s_-]+", "_", column.strip().casefold())
        target = aliases.get(normalized)
        if target and target not in used_targets:
            mapping[column] = target
            used_targets.add(target)
    return mapping


def map_and_validate_dataframe(
    dataframe: pd.DataFrame,
    *,
    mapping: dict[str, str],
    normalization: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = validate_connector_mapping(mapping, normalization)
    rows_read = len(dataframe.index)
    if rows_read == 0:
        raise IntegrationValidationError(
            "File không chứa bản ghi dữ liệu.",
            {
                "status": "failed",
                "rows_read": 0,
                "rows_valid": 0,
                "rows_rejected": 0,
                "issues": [],
                "error": "File không chứa bản ghi dữ liệu.",
            },
        )
    if rows_read > MAX_INTEGRATION_ROWS:
        raise IntegrationValidationError(
            f"File vượt quá giới hạn {MAX_INTEGRATION_ROWS:,} dòng.",
            {
                "status": "failed",
                "rows_read": rows_read,
                "rows_valid": 0,
                "rows_rejected": rows_read,
                "issues": [],
                "error": f"Vượt quá giới hạn {MAX_INTEGRATION_ROWS:,} dòng.",
            },
        )

    duplicate_columns = dataframe.columns[
        dataframe.columns.duplicated()
    ].unique().tolist()
    if duplicate_columns:
        raise IntegrationValidationError(
            "File chứa tên cột trùng lặp; hãy đổi tên các cột trước khi nhập.",
            {
                "status": "failed",
                "rows_read": rows_read,
                "rows_valid": 0,
                "rows_rejected": rows_read,
                "duplicate_columns": [str(column) for column in duplicate_columns],
                "issues": [],
            },
        )

    missing_columns = sorted(set(mapping) - set(dataframe.columns))
    if missing_columns:
        raise IntegrationValidationError(
            "Không tìm thấy các cột đã cấu hình trong file.",
            {
                "status": "failed",
                "rows_read": rows_read,
                "rows_valid": 0,
                "rows_rejected": rows_read,
                "missing_source_columns": missing_columns,
                "issues": [],
            },
        )

    source_to_target = mapping
    timezone = ZoneInfo(config["date_timezone"])
    downtime_factor = _DOWNTIME_TO_MINUTES[config["downtime_unit"]]
    uppercase_fields = set(config["uppercase_code_fields"])
    source_columns = list(source_to_target)
    column_indexes = {
        source_column: dataframe.columns.get_loc(source_column)
        for source_column in source_columns
    }
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    rejected = 0

    for row_number, source_row in enumerate(
        dataframe.itertuples(index=False, name=None),
        start=2,
    ):
        record: dict[str, Any] = {}
        attributes: dict[str, Any] = {}
        row_issues: list[dict[str, str]] = []
        for source_column, target in source_to_target.items():
            value = source_row[column_indexes[source_column]]
            if target.startswith("attributes."):
                if pd.isna(value):
                    continue
                attributes[target.removeprefix("attributes.")] = str(value).strip()
                continue

            if target == "Date":
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    row_issues.append({"field": target, "code": "required"})
                    continue
                try:
                    timestamp = pd.Timestamp(value)
                    if pd.isna(timestamp):
                        raise ValueError
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.tz_localize(
                            timezone,
                            ambiguous="raise",
                            nonexistent="raise",
                        )
                    else:
                        timestamp = timestamp.tz_convert(timezone)
                    record[target] = timestamp.tz_convert("UTC").isoformat()
                except (TypeError, ValueError, OverflowError):
                    row_issues.append({"field": target, "code": "invalid_date"})
                continue

            if target in NUMERIC_FIELDS:
                if pd.isna(value) or (
                    isinstance(value, str) and not value.strip()
                ):
                    if target in REQUIRED_FIELDS:
                        row_issues.append({"field": target, "code": "required"})
                    else:
                        record[target] = None
                    continue
                parsed, issue = _parse_numeric(value)
                if parsed is None or issue == "invalid":
                    row_issues.append(
                        {"field": target, "code": "invalid_number"}
                    )
                    continue
                if issue == "negative":
                    row_issues.append(
                        {"field": target, "code": "negative_number"}
                    )
                    continue
                if target == "Downtime":
                    parsed = float(parsed) * downtime_factor
                record[target] = parsed
                continue

            if pd.isna(value):
                record[target] = None
                continue
            normalized_value = str(value).strip()
            if target in uppercase_fields:
                normalized_value = normalized_value.upper()
            record[target] = normalized_value or None

        if attributes:
            record["attributes"] = attributes

        if row_issues:
            rejected += 1
            if len(issues) < MAX_VALIDATION_SAMPLES:
                issues.extend(
                    {"row": row_number, **issue}
                    for issue in row_issues[: MAX_VALIDATION_SAMPLES - len(issues)]
                )
        else:
            records.append(record)

    report = {
        "status": "failed" if rejected else "valid",
        "rows_read": rows_read,
        "rows_valid": len(records),
        "rows_rejected": rejected,
        "issues": issues,
        "normalization": {
            "date_timezone": config["date_timezone"],
            "stored_timezone": "UTC",
            "downtime_source_unit": config["downtime_unit"],
            "downtime_target_unit": "minutes",
            "uppercase_code_fields": sorted(uppercase_fields),
        },
    }
    if rejected:
        report["error"] = (
            "Có dòng dữ liệu không hợp lệ; không có dòng nào được nhập. "
            "Sửa các dòng lỗi rồi tải lại file."
        )
        raise IntegrationValidationError(report["error"], report)
    return records, report
