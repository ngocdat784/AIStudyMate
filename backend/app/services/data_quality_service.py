
from __future__ import annotations

from typing import Any

import pandas as pd


# =========================================================
# Standard field definitions
# =========================================================

FIELD_LABELS = {
    "product": "Product",
    "model": "Model",
    "component": "Component",
    "supplier": "Supplier",
    "batch": "Batch",
    "production": "Production",
    "defects": "Defects",
    "downtime": "Downtime",
    "date": "Date",
}

NUMERIC_FIELDS = (
    "production",
    "defects",
    "downtime",
)

REQUIRED_FIELDS = tuple(FIELD_LABELS.keys())


# =========================================================
# Column aliases
# =========================================================

COLUMN_ALIASES = {
    "product": [
        "product",
        "san_pham",
        "sản phẩm",
    ],
    "model": [
        "model",
        "product_model",
        "product model",
    ],
    "component": [
        "component",
        "linh_kien",
        "linh kiện",
    ],
    "supplier": [
        "supplier",
        "nha_cung_cap",
        "nhà cung cấp",
    ],
    "batch": [
        "batch",
        "lo_san_xuat",
        "lô sản xuất",
    ],
    "production": [
        "production",
        "output",
        "quantity",
        "san_luong",
        "sản lượng",
    ],
    "defects": [
        "defects",
        "defect",
        "defect_count",
        "loi",
        "lỗi",
        "số lỗi",
    ],
    "downtime": [
        "downtime",
        "downtime_minutes",
        "thoi_gian_dung_may",
        "thời gian dừng máy",
    ],
    "date": [
        "date",
        "production_date",
        "ngay",
        "ngày",
        "ngày sản xuất",
    ],
}


# =========================================================
# Utility functions
# =========================================================

def _normalize_column(value: Any) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace(" ", "_")
    )


def _resolve_columns(
    dataframe: pd.DataFrame,
) -> dict[str, str | None]:
    normalized = {
        _normalize_column(column): column
        for column in dataframe.columns
    }

    result: dict[str, str | None] = {}

    for field, aliases in COLUMN_ALIASES.items():
        result[field] = next(
            (
                normalized[_normalize_column(alias)]
                for alias in aliases
                if _normalize_column(alias) in normalized
            ),
            None,
        )

    return result


def _is_missing(series: pd.Series) -> pd.Series:
    """Detect NA values and empty strings."""
    return (
        series.isna()
        | series.astype("string")
        .str.strip()
        .eq("")
        .fillna(False)
    )


def _to_numeric(series: pd.Series) -> pd.Series:
    """
    Convert numeric values.

    Supports comma-separated thousands and a trailing percent sign.
    Assumes commas are thousands separators, not decimal separators.
    """
    values = (
        series.astype("string")
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
    )

    return pd.to_numeric(
        values,
        errors="coerce",
    )


def _to_date(series: pd.Series) -> pd.Series:
    """Convert dates; invalid non-empty values become NaT."""
    return pd.to_datetime(
        series,
        errors="coerce",
    )


# =========================================================
# Data Quality Engine
# =========================================================

def calculate_data_quality(
    dataframe: pd.DataFrame,
) -> dict[str, Any]:
    """
    Inspect data quality without modifying the input DataFrame.

    Policy:
    - Completely empty rows are excluded from data_rows.
    - A row with any required-field issue is considered invalid.
    - Invalid numeric values, negative metrics, and invalid dates
      make the affected row invalid.
    - Duplicate occurrences after the first are also invalid rows.
    - A row with multiple issues is counted only once in invalid_rows.
    - Missing entire required columns are reported separately.
    - Only fields in FIELD_LABELS are required for data-quality scoring.
    """

    if dataframe.empty:
        df = dataframe.copy()
    else:
        df = dataframe.copy()

    column_map = _resolve_columns(df)

    # -----------------------------------------------------
    # 1. Empty rows and duplicate rows
    # -----------------------------------------------------

    if df.empty:
        empty_mask = pd.Series(
            dtype=bool,
            index=df.index,
        )
    else:
        empty_mask = df.apply(
            lambda row: _is_missing(row).all(),
            axis=1,
        )

    data_df = df.loc[~empty_mask].copy()

    total_rows = len(df)
    empty_rows = int(empty_mask.sum())
    data_rows = len(data_df)

    # Count repeated occurrences, keeping the first occurrence.
    duplicate_mask = data_df.duplicated(
        keep="first",
    )

    duplicate_rows = int(duplicate_mask.sum())

    # -----------------------------------------------------
    # 2. Missing required columns
    # -----------------------------------------------------

    missing_columns = [
        FIELD_LABELS[field]
        for field in REQUIRED_FIELDS
        if column_map.get(field) is None
    ]

    missing_values: dict[str, int] = {}
    invalid_values: dict[str, int] = {}
    negative_values: dict[str, int] = {}

    # A row is invalid if it has one or more data-quality issues.
    invalid_row_mask = pd.Series(
        False,
        index=data_df.index,
        dtype=bool,
    )

    # Duplicate occurrences are invalid for quality scoring.
    invalid_row_mask |= duplicate_mask

    # -----------------------------------------------------
    # 3. Required-field missing values
    # -----------------------------------------------------

    for field, label in FIELD_LABELS.items():
        actual_column = column_map.get(field)

        if actual_column is None:
            # The entire column is missing.
            missing_values[label] = data_rows

            if data_rows > 0:
                invalid_row_mask[:] = True

            continue

        missing_mask = _is_missing(
            data_df[actual_column]
        )

        missing_values[label] = int(
            missing_mask.sum()
        )

        invalid_row_mask |= missing_mask

    # -----------------------------------------------------
    # 4. Numeric validation
    # -----------------------------------------------------

    for field in NUMERIC_FIELDS:
        label = FIELD_LABELS[field]
        actual_column = column_map.get(field)

        if actual_column is None:
            # The missing column is already reported above.
            invalid_values[label] = 0
            negative_values[label] = 0
            continue

        raw = data_df[actual_column]

        missing_mask = _is_missing(raw)
        converted = _to_numeric(raw)

        # A non-empty value that cannot be parsed as numeric.
        invalid_mask = (
            ~missing_mask
            & converted.isna()
        )

        # Negative values are counted separately from malformed values.
        negative_mask = converted.lt(0).fillna(False)

        invalid_values[label] = int(
            invalid_mask.sum()
        )

        negative_values[label] = int(
            negative_mask.sum()
        )

        invalid_row_mask |= (
            invalid_mask | negative_mask
        )

    # -----------------------------------------------------
    # 5. Date validation
    # -----------------------------------------------------

    actual_date_column = column_map.get("date")

    if actual_date_column is not None:
        raw_date = data_df[actual_date_column]

        missing_date_mask = _is_missing(raw_date)
        converted_date = _to_date(raw_date)

        invalid_date_mask = (
            ~missing_date_mask
            & converted_date.isna()
        )

        invalid_values["Date"] = int(
            invalid_date_mask.sum()
        )

        invalid_row_mask |= invalid_date_mask

    else:
        invalid_values["Date"] = 0

    # -----------------------------------------------------
    # 6. Valid and invalid rows
    # -----------------------------------------------------

    valid_rows = int(
        (~invalid_row_mask).sum()
    )

    # Includes invalid rows and duplicate occurrences,
    # but does not include completely empty rows.
    invalid_rows = data_rows - valid_rows

    # -----------------------------------------------------
    # 7. Quality score and status
    # -----------------------------------------------------

    quality_score = (
        round(valid_rows / data_rows * 100, 1)
        if data_rows > 0
        else 0.0
    )

    if (
        quality_score >= 95
        and not missing_columns
    ):
        quality_status = "good"
    elif (
        quality_score >= 80
        and not missing_columns
    ):
        quality_status = "warning"
    else:
        quality_status = "poor"

    # -----------------------------------------------------
    # 8. Warnings
    # -----------------------------------------------------

    warnings: list[str] = []

    for label, count in missing_values.items():
        if count > 0:
            warnings.append(
                f"{count} bản ghi bị thiếu thông tin {label}."
            )

    for label, count in invalid_values.items():
        if count > 0:
            warnings.append(
                f"{count} bản ghi có giá trị {label} không hợp lệ."
            )

    for label, count in negative_values.items():
        if count > 0:
            warnings.append(
                f"{count} bản ghi có giá trị {label} âm."
            )

    if empty_rows > 0:
        warnings.append(
            f"Có {empty_rows} dòng trống trong file."
        )

    if duplicate_rows > 0:
        warnings.append(
            f"Có {duplicate_rows} bản ghi trùng lặp; "
            "các lần xuất hiện trùng sau bản ghi đầu "
            "được tính là dòng không hợp lệ."
        )

    if missing_columns:
        warnings.append(
            "Thiếu cột bắt buộc: "
            + ", ".join(missing_columns)
            + "."
        )

    if data_rows == 0:
        warnings.append(
            "File không có bản ghi dữ liệu để kiểm định."
        )

    # -----------------------------------------------------
    # 9. Result
    # -----------------------------------------------------

    return {
        "quality_score": quality_score,
        "quality_status": quality_status,
        "total_rows": total_rows,
        "data_rows": data_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "empty_rows": empty_rows,
        "duplicate_rows": duplicate_rows,
        "missing_values": missing_values,
        "invalid_values": invalid_values,
        "negative_values": negative_values,
        "missing_columns": missing_columns,
        "warnings": warnings,
        "scoring_basis": (
            "valid_rows / data_rows * 100; "
            "duplicate occurrences count as invalid rows; "
            "empty rows are excluded from data_rows."
        ),
    }
