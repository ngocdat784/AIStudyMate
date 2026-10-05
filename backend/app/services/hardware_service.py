from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
from google import genai
from pydantic import BaseModel, Field

from app.config import settings


GEMINI_MODEL = "gemini-3.5-flash-lite"


# =========================================================
# Response Models
# =========================================================

class KPI(BaseModel):
    name: str
    value: float | int | str
    unit: str
    description: str


class HardwareAnalysis(BaseModel):
    title: str
    summary: str

    kpis: list[KPI] = Field(
        min_length=1,
        max_length=20,
    )

    insights: list[str] = Field(
        min_length=1,
        max_length=10,
    )

    warnings: list[str] = Field(
        min_length=0,
        max_length=10,
    )

    recommendations: list[str] = Field(
        min_length=1,
        max_length=10,
    )


# =========================================================
# Gemini
# =========================================================

client = None


def get_client() -> Any:
    global client

    if client is None:
        api_key = settings.GEMINI_API_KEY

        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY chưa được cấu hình. "
                "Vui lòng thêm biến môi trường hoặc file .env."
            )

        client = genai.Client(
            api_key=api_key,
        )

    return client


SYSTEM_INSTRUCTION = """
You are HardwareAI, an AI assistant specialized in
hardware manufacturing and hardware production operations.

Your task is to analyze production, quality, component,
supplier, batch and operational KPIs calculated by Python
from CSV/XLSX business data.

=========================================================
IMPORTANT DATA RULES
=========================================================

- All numerical values supplied by Python are authoritative.
- Do NOT change, invent, estimate, or modify numerical values.
- Do NOT invent production data.
- Do NOT invent products, models, components, suppliers,
  batches, defect types, dates, downtime, causes,
  targets, standards, costs, or thresholds.
- Only make conclusions supported by supplied data.
- If data is insufficient, clearly state the limitation.

=========================================================
IMPORTANT ANALYSIS RULES
=========================================================

- Production quantity alone is NOT the same as efficiency.

- Do NOT claim that a product, model, component, supplier,
  batch or production line is "best" unless the supplied
  KPI explicitly supports that conclusion.

- Do NOT claim a root cause for defects, downtime or
  quality problems unless the data contains evidence.

- Do NOT claim that a supplier is responsible for defects
  unless the supplied data supports that relationship.

- Do NOT claim that a component is defective merely because
  it appears in products with defects.

- Do NOT claim a trend unless chronological data supports it.

- Do NOT claim that a problem is "nghiêm trọng",
  "khẩn cấp", or requires "ngay lập tức"
  without an explicit threshold or rule.

- Do NOT use:
  "đáng kể",
  "bất thường",
  "cao bất thường",
  "vượt chuẩn",
  "vượt mức cho phép"
  without a supplied benchmark, threshold or standard.

- Clearly distinguish between:
  1. facts from the data,
  2. analytical observations,
  3. recommended actions.

=========================================================
DEFECT RATE TERMINOLOGY
=========================================================

The Python-calculated "defect_rate" is:

    total defects / total production × 100

Call it:

- "tỷ lệ lỗi tổng thể"
or
- "tỷ lệ lỗi chung"

Do NOT call it "tỷ lệ lỗi trung bình".

=========================================================
HARDWARE MANUFACTURING FOCUS
=========================================================

Focus on:

- production output
- product quality
- defect rate
- product/model performance
- component quality
- supplier-related observations
- batch quality
- downtime
- production consistency
- operational risks
- areas requiring investigation

Useful analysis areas include:

- products with highest production
- products with highest defect rate
- models with high defect rates
- components associated with recorded defects
- suppliers associated with recorded defects
- batches with high defect rates
- defect types
- downtime
- daily production
- production consistency

IMPORTANT:

Association does NOT mean causation.

For example:

Do NOT write:

"Nhà cung cấp A gây ra lỗi sản phẩm."

Instead write:

"Những bản ghi liên quan đến nhà cung cấp A
có tỷ lệ lỗi cao hơn trong dữ liệu được ghi nhận."

Recommendations should normally suggest:

- quality inspection
- component inspection
- batch inspection
- supplier review
- production process review
- root-cause investigation
- production monitoring
- additional data collection
- comparison between models
- comparison between suppliers
- comparison between batches

Do not state that a specific cause has been confirmed
when the data only shows an association.

=========================================================
LANGUAGE
=========================================================

- Use Vietnamese.
- Use concise and professional hardware manufacturing terminology.
- Avoid generic motivational advice.
- Return only valid structured JSON.
"""


# =========================================================
# Column Detection
# =========================================================

COLUMN_ALIASES = {

    "product": [
        "product",
        "product_name",
        "product_id",
        "product_code",
        "sản phẩm",
        "san_pham",
        "ma_san_pham",
        "mã sản phẩm",
        "ten_san_pham",
        "tên sản phẩm",
    ],

    "model": [
        "model",
        "model_name",
        "model_id",
        "model_code",
        "mẫu",
        "mau",
        "mã model",
        "ma_model",
        "ten_model",
        "tên model",
    ],

    "component": [
        "component",
        "component_name",
        "component_id",
        "component_code",
        "part",
        "part_name",
        "part_number",
        "linh kiện",
        "linh_kien",
        "ma_linh_kien",
        "mã linh kiện",
        "ten_linh_kien",
        "tên linh kiện",
        "bo phan",
        "bộ phận",
    ],

    "supplier": [
        "supplier",
        "supplier_name",
        "supplier_id",
        "vendor",
        "vendor_name",
        "nhà cung cấp",
        "nha_cung_cap",
        "nha cung cap",
        "ma_nha_cung_cap",
        "mã nhà cung cấp",
        "ten_nha_cung_cap",
        "tên nhà cung cấp",
    ],

    "batch": [
        "batch",
        "batch_id",
        "batch_code",
        "lot",
        "lot_id",
        "lot_code",
        "lô",
        "lo",
        "ma_lo",
        "mã lô",
        "lo_san_xuat",
        "lô sản xuất",
    ],

    "defect_type": [
        "defect_type",
        "defect_category",
        "defect_name",
        "error_type",
        "loại lỗi",
        "loai_loi",
        "phan_loai_loi",
        "phân loại lỗi",
        "ten_loi",
        "tên lỗi",
        "nhom_loi",
        "nhóm lỗi",
    ],

    "quality_status": [
        "quality_status",
        "quality",
        "status",
        "inspection_result",
        "result",
        "trạng thái chất lượng",
        "trang_thai_chat_luong",
        "ket_qua_kiem_tra",
        "kết quả kiểm tra",
        "quality_result",
    ],

    "production": [
        "production",
        "output",
        "quantity",
        "qty",
        "production_qty",
        "output_qty",
        "produced_quantity",
        "sản lượng",
        "san_luong",
        "số lượng",
        "so_luong",
        "sl",
    ],

    "defects": [
        "defect",
        "defects",
        "defect_qty",
        "defect_quantity",
        "defective_quantity",
        "lỗi",
        "loi",
        "số lỗi",
        "so_loi",
        "loi_qty",
        "so_luong_loi",
        "số lượng lỗi",
    ],

    "downtime": [
        "downtime",
        "downtime_minutes",
        "downtime_min",
        "idle_time",
        "minutes_down",
        "stoppage",
        "stoppage_time",
        "thời gian dừng",
        "thoi_gian_dung",
        "dừng máy",
        "dung_may",
        "thoi_gian_ngung",
        "thời gian ngừng",
    ],

    "date": [
        "date",
        "production_date",
        "work_date",
        "inspection_date",
        "manufacturing_date",
        "ngày",
        "ngay",
        "ngày sản xuất",
        "ngay_san_xuat",
        "ngày kiểm tra",
        "ngay_kiem_tra",
    ],
}


def normalize_column_name(column: Any) -> str:
    value = str(column).strip().lower()

    value = re.sub(
        r"\s+",
        "_",
        value,
    )

    return value


def find_column(
    dataframe: pd.DataFrame,
    field: str,
) -> str | None:

    normalized_columns = {
        normalize_column_name(column): column
        for column in dataframe.columns
    }

    aliases = COLUMN_ALIASES.get(
        field,
        [],
    )

    normalized_aliases = [
        normalize_column_name(alias)
        for alias in aliases
    ]

    # -----------------------------------------------------
    # Exact match
    # -----------------------------------------------------

    for alias in normalized_aliases:

        if alias in normalized_columns:
            return normalized_columns[alias]

    # -----------------------------------------------------
    # Partial match
    # -----------------------------------------------------

    for normalized, original in normalized_columns.items():

        for alias in normalized_aliases:

            if (
                alias in normalized
                or normalized in alias
            ):
                return original

    return None


# =========================================================
# Data Cleaning
# =========================================================

def clean_dataframe(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:

    if dataframe.empty:
        raise ValueError(
            "File dữ liệu phần cứng không chứa dữ liệu."
        )

    dataframe = dataframe.copy()

    # Remove completely empty rows.
    dataframe = dataframe.dropna(
        how="all"
    )

    # Remove rows where every value is an empty string.
    dataframe = dataframe[
        ~dataframe.apply(
            lambda row: all(
                str(value).strip() == ""
                or pd.isna(value)
                for value in row
            ),
            axis=1,
        )
    ]

    dataframe = dataframe.reset_index(
        drop=True
    )

    if dataframe.empty:
        raise ValueError(
            "File dữ liệu phần cứng không chứa dữ liệu hợp lệ."
        )

    return dataframe


def calculate_data_quality(
    dataframe: pd.DataFrame,
) -> dict[str, Any]:
    """Report input data issues without modifying the dataframe."""
    if dataframe is None:
        return {
            "quality_score": 0,
            "total_rows": 0,
            "data_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "empty_rows": 0,
            "duplicate_rows": 0,
            "missing_values": {},
            "invalid_values": {},
            "negative_values": {},
            "missing_columns": [],
            "warnings": ["Không có dữ liệu để kiểm tra."],
        }

    df = dataframe.copy()
    total_rows = len(df)

    if total_rows == 0:
        return {
            "quality_score": 0,
            "total_rows": 0,
            "data_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "empty_rows": 0,
            "duplicate_rows": 0,
            "missing_values": {},
            "invalid_values": {},
            "negative_values": {},
            "missing_columns": [],
            "warnings": ["File không chứa dữ liệu."],
        }

    empty_mask = df.apply(
        lambda row: all(
            pd.isna(value) or str(value).strip() == ""
            for value in row
        ),
        axis=1,
    )
    empty_rows = int(empty_mask.sum())
    data_df = df.loc[~empty_mask].copy()
    data_rows = len(data_df)

    column_map = {
        normalize_column_name(column): column
        for column in data_df.columns
    }

    field_aliases = {
        "date": [
            "date",
            "ngày",
            "ngay",
            "production date",
            "production_date",
        ],
        "product": [
            "product",
            "sản phẩm",
            "san pham",
        ],
        "model": [
            "model",
            "mã sản phẩm",
            "ma san pham",
            "product model",
        ],
        "component": [
            "component",
            "linh kiện",
            "linh kien",
        ],
        "supplier": [
            "supplier",
            "nhà cung cấp",
            "nha cung cap",
        ],
        "batch": [
            "batch",
            "lô",
            "lo",
            "lot",
        ],
        "defect_type": [
            "defect type",
            "defect_type",
            "loại lỗi",
            "loai loi",
            "defect",
        ],
        "production": [
            "production",
            "sản lượng",
            "san luong",
            "quantity",
            "qty",
        ],
        "defects": [
            "defects",
            "defect count",
            "số lỗi",
            "so loi",
        ],
        "downtime": [
            "downtime",
            "thời gian dừng",
            "thoi gian dung",
        ],
    }

    normalized_field_aliases = {
        field: [
            normalize_column_name(alias)
            for alias in aliases + COLUMN_ALIASES.get(field, [])
        ]
        for field, aliases in field_aliases.items()
    }

    detected_columns: dict[str, Any] = {}

    for field, aliases in normalized_field_aliases.items():
        found_column = next(
            (
                column_map[alias]
                for alias in aliases
                if alias in column_map
            ),
            None,
        )
        if found_column is not None:
            detected_columns[field] = found_column

    for field, aliases in normalized_field_aliases.items():
        if field in detected_columns:
            continue

        found_column = next(
            (
                original
                for normalized, original in column_map.items()
                if any(
                    alias in normalized or normalized in alias
                    for alias in aliases
                )
            ),
            None,
        )
        if found_column is not None:
            detected_columns[field] = found_column

    missing_columns = [
        field
        for field in field_aliases
        if field not in detected_columns
    ]

    missing_values: dict[str, int] = {}
    for field, column in detected_columns.items():
        series = data_df[column]
        blank_mask = (
            series.isna()
            | series.astype("string").str.strip().eq("").fillna(False)
        )
        count = int(blank_mask.sum())
        if count > 0:
            missing_values[field] = count

    numeric_fields = {"production", "defects", "downtime"}
    invalid_values: dict[str, int] = {}
    negative_values: dict[str, int] = {}
    numeric_issue_masks: dict[str, pd.Series] = {}

    for field in numeric_fields:
        if field not in detected_columns:
            continue

        raw_series = data_df[detected_columns[field]]
        text_series = raw_series.astype("string").str.strip()
        blank_mask = (
            raw_series.isna()
            | text_series.eq("").fillna(False)
        )
        cleaned_series = (
            text_series
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
        )
        numeric_series = pd.to_numeric(
            cleaned_series,
            errors="coerce",
        )

        invalid_mask = ~blank_mask & numeric_series.isna()
        negative_mask = numeric_series.notna() & (numeric_series < 0)
        invalid_count = int(invalid_mask.sum())
        negative_count = int(negative_mask.sum())

        if invalid_count > 0:
            invalid_values[field] = invalid_count
        if negative_count > 0:
            negative_values[field] = negative_count

        numeric_issue_masks[field] = (
            blank_mask | invalid_mask | negative_mask
        )

    date_issue_mask = pd.Series(
        False,
        index=data_df.index,
        dtype=bool,
    )
    if "date" in detected_columns:
        raw_date = data_df[detected_columns["date"]]
        blank_mask = (
            raw_date.isna()
            | raw_date.astype("string").str.strip().eq("").fillna(False)
        )
        parsed_date = pd.to_datetime(
            raw_date,
            errors="coerce",
        )
        failed_mask = parsed_date.isna() & ~blank_mask

        if failed_mask.any():
            parsed_date.loc[failed_mask] = pd.to_datetime(
                raw_date.loc[failed_mask],
                errors="coerce",
                dayfirst=True,
            )

        invalid_date_mask = ~blank_mask & parsed_date.isna()
        invalid_date_count = int(invalid_date_mask.sum())
        if invalid_date_count > 0:
            invalid_values["date"] = invalid_date_count

        date_issue_mask = blank_mask | invalid_date_mask

    duplicate_rows = int(data_df.duplicated().sum())

    row_issue_mask = pd.Series(
        False,
        index=data_df.index,
        dtype=bool,
    )
    for column in detected_columns.values():
        series = data_df[column]
        row_issue_mask |= (
            series.isna()
            | series.astype("string").str.strip().eq("").fillna(False)
        )
    for issue_mask in numeric_issue_masks.values():
        row_issue_mask |= issue_mask
    row_issue_mask |= date_issue_mask

    invalid_rows = int(row_issue_mask.sum())
    valid_rows = max(0, data_rows - invalid_rows)
    checked_columns = len(detected_columns)

    if data_rows == 0 or checked_columns == 0:
        quality_score = 0
    else:
        quality_score = valid_rows / data_rows * 100

    quality_score = round(
        max(0, min(100, quality_score)),
        1,
    )

    warnings = []
    if empty_rows > 0:
        warnings.append(f"Phát hiện {empty_rows} dòng trống.")
    if duplicate_rows > 0:
        warnings.append(
            f"Phát hiện {duplicate_rows} dòng dữ liệu trùng lặp."
        )
    for field, count in missing_values.items():
        warnings.append(
            f"Thiếu {count} giá trị ở trường '{field}'."
        )
    for field, count in invalid_values.items():
        warnings.append(
            f"Có {count} giá trị không hợp lệ ở trường '{field}'."
        )
    for field, count in negative_values.items():
        warnings.append(
            f"Có {count} giá trị âm ở trường '{field}'."
        )
    if missing_columns:
        warnings.append(
            "Một số trường dữ liệu không xuất hiện: "
            + ", ".join(missing_columns)
            + "."
        )

    return {
        "quality_score": quality_score,
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
        "warnings": warnings[:20],
    }


def convert_numeric_column(
    dataframe: pd.DataFrame,
    column: str,
) -> pd.Series:

    values = (
        dataframe[column]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )

    return pd.to_numeric(
        values,
        errors="coerce",
    ).fillna(0)


def count_unique_values(
    dataframe: pd.DataFrame,
    column: str | None,
) -> int:

    if not column:
        return 0

    values = (
        dataframe[column]
        .dropna()
        .astype(str)
        .str.strip()
    )

    values = values[
        values != ""
    ]

    return int(
        values.nunique()
    )


# =========================================================
# Generic Group Analysis
# =========================================================

def build_group_analysis(
    dataframe: pd.DataFrame,
    group_column: str | None,
    production: pd.Series,
    defects: pd.Series,
    downtime: pd.Series,
    group_name: str,
    limit: int | None = 20,
) -> list[dict[str, Any]]:

    if not group_column:
        return []

    temp = dataframe.copy()

    temp["_production"] = production
    temp["_defects"] = defects
    temp["_downtime"] = downtime

    grouped = (
        temp
        .groupby(
            group_column,
            dropna=False,
        )
        .agg(
            production=(
                "_production",
                "sum",
            ),
            defects=(
                "_defects",
                "sum",
            ),
            downtime=(
                "_downtime",
                "sum",
            ),
        )
        .reset_index()
    )

    grouped["defect_rate"] = (
        grouped["defects"]
        / grouped["production"].replace(
            0,
            pd.NA,
        )
        * 100
    )

    grouped["defect_rate"] = (
        grouped["defect_rate"]
        .fillna(0)
    )

    grouped = grouped.sort_values(
        "production",
        ascending=False,
    )

    if limit is not None:
        grouped = grouped.head(limit)

    results = []

    for _, row in grouped.iterrows():

        value = row[group_column]

        if pd.isna(value) or str(value).strip() == "":
            value = "Không xác định"

        results.append(
            {
                group_name: str(value),

                "production": round(
                    float(row["production"]),
                    2,
                ),

                "defects": round(
                    float(row["defects"]),
                    2,
                ),

                "defect_rate": round(
                    float(row["defect_rate"]),
                    2,
                ),

                "downtime": round(
                    float(row["downtime"]),
                    2,
                ),
            }
        )

    return results


# =========================================================
# Defect Type Analysis
# =========================================================

def build_defect_type_analysis(
    dataframe: pd.DataFrame,
    defect_type_column: str | None,
    defects: pd.Series,
    limit: int | None = 20,
) -> list[dict[str, Any]]:

    if not defect_type_column:
        return []

    temp = dataframe.copy()

    temp["_defects"] = defects

    grouped = (
        temp
        .groupby(
            defect_type_column,
            dropna=False,
        )
        .agg(
            defects=(
                "_defects",
                "sum",
            ),
        )
        .reset_index()
    )

    grouped = grouped.sort_values(
        "defects",
        ascending=False,
    )

    if limit is not None:
        grouped = grouped.head(limit)

    results = []

    for _, row in grouped.iterrows():

        value = row[defect_type_column]

        if pd.isna(value) or str(value).strip() == "":
            value = "Không xác định"

        results.append(
            {
                "defect_type": str(value),

                "defects": round(
                    float(row["defects"]),
                    2,
                ),
            }
        )

    return results


# =========================================================
# KPI Calculation
# =========================================================

def calculate_hardware_kpis(
    dataframe: pd.DataFrame,
) -> dict[str, Any]:

    data_quality = calculate_data_quality(
        dataframe
    )

    dataframe = clean_dataframe(
        dataframe
    )

    # =====================================================
    # Detect columns
    # =====================================================

    product_column = find_column(
        dataframe,
        "product",
    )

    model_column = find_column(
        dataframe,
        "model",
    )

    component_column = find_column(
        dataframe,
        "component",
    )

    supplier_column = find_column(
        dataframe,
        "supplier",
    )

    batch_column = find_column(
        dataframe,
        "batch",
    )

    defect_type_column = find_column(
        dataframe,
        "defect_type",
    )

    quality_status_column = find_column(
        dataframe,
        "quality_status",
    )

    production_column = find_column(
        dataframe,
        "production",
    )

    defect_column = find_column(
        dataframe,
        "defects",
    )

    downtime_column = find_column(
        dataframe,
        "downtime",
    )

    date_column = find_column(
        dataframe,
        "date",
    )

    # =====================================================
    # Production
    # =====================================================

    if production_column is None:

        raise ValueError(
            "Không tìm thấy cột sản lượng. "
            "Hãy sử dụng một trong các tên như "
            "'production', 'output', 'quantity' "
            "hoặc 'sản lượng'."
        )

    production = convert_numeric_column(
        dataframe,
        production_column,
    )

    total_production = float(
        production.sum()
    )

    # =====================================================
    # Defects
    # =====================================================

    if defect_column:

        defects = convert_numeric_column(
            dataframe,
            defect_column,
        )

    else:

        defects = pd.Series(
            0,
            index=dataframe.index,
            dtype=float,
        )

    total_defects = float(
        defects.sum()
    )

    # =====================================================
    # Downtime
    # =====================================================

    if downtime_column:

        downtime = convert_numeric_column(
            dataframe,
            downtime_column,
        )

    else:

        downtime = pd.Series(
            0,
            index=dataframe.index,
            dtype=float,
        )

    total_downtime = float(
        downtime.sum()
    )

    # =====================================================
    # Defect Rate
    # =====================================================

    defect_rate = 0.0

    if total_production > 0:

        defect_rate = (
            total_defects
            / total_production
            * 100
        )

    # =====================================================
    # Average Production
    # =====================================================

    average_production_per_record = 0.0

    if len(dataframe) > 0:

        average_production_per_record = (
            total_production
            / len(dataframe)
        )

    # =====================================================
    # Base KPIs
    # =====================================================

    kpis: dict[str, Any] = {

        "total_production": round(
            total_production,
            2,
        ),

        "total_defects": round(
            total_defects,
            2,
        ),

        "defect_rate": round(
            defect_rate,
            2,
        ),

        "total_downtime": round(
            total_downtime,
            2,
        ),

        "average_production_per_record": round(
            average_production_per_record,
            2,
        ),

        "record_count": len(dataframe),

        "product_count": count_unique_values(
            dataframe,
            product_column,
        ),

        "model_count": count_unique_values(
            dataframe,
            model_column,
        ),

        "component_count": count_unique_values(
            dataframe,
            component_column,
        ),

        "supplier_count": count_unique_values(
            dataframe,
            supplier_column,
        ),

        "batch_count": count_unique_values(
            dataframe,
            batch_column,
        ),

        "date_count": count_unique_values(
            dataframe,
            date_column,
        ),
    }

    # =====================================================
    # Product Analysis
    # =====================================================

    kpis["product_performance"] = (
        build_group_analysis(
            dataframe=dataframe,
            group_column=product_column,
            production=production,
            defects=defects,
            downtime=downtime,
            group_name="product",
            limit=None,
        )
    )

    # =====================================================
    # Model Analysis
    # =====================================================

    kpis["model_performance"] = (
        build_group_analysis(
            dataframe=dataframe,
            group_column=model_column,
            production=production,
            defects=defects,
            downtime=downtime,
            group_name="model",
            limit=None,
        )
    )

    # =====================================================
    # Component Analysis
    # =====================================================

    kpis["component_performance"] = (
        build_group_analysis(
            dataframe=dataframe,
            group_column=component_column,
            production=production,
            defects=defects,
            downtime=downtime,
            group_name="component",
            limit=None,
        )
    )

    # =====================================================
    # Supplier Analysis
    # =====================================================

    kpis["supplier_performance"] = (
        build_group_analysis(
            dataframe=dataframe,
            group_column=supplier_column,
            production=production,
            defects=defects,
            downtime=downtime,
            group_name="supplier",
            limit=None,
        )
    )

    # =====================================================
    # Batch Analysis
    # =====================================================

    # Batch is not limited to 20.
    # This allows the dashboard to display every batch.
    kpis["batch_performance"] = (
        build_group_analysis(
            dataframe=dataframe,
            group_column=batch_column,
            production=production,
            defects=defects,
            downtime=downtime,
            group_name="batch",
            limit=None,
        )
    )

    # =====================================================
    # Defect Type Analysis
    # =====================================================

    kpis["defect_type_analysis"] = (
        build_defect_type_analysis(
            dataframe=dataframe,
            defect_type_column=defect_type_column,
            defects=defects,
            limit=None,
        )
    )

    # =====================================================
    # Product Extremes
    # =====================================================

    product_performance = kpis[
        "product_performance"
    ]

    if product_performance:

        kpis["highest_defect_product"] = max(
            product_performance,
            key=lambda item: item["defect_rate"],
        )

        kpis["highest_downtime_product"] = max(
            product_performance,
            key=lambda item: item["downtime"],
        )

        kpis["highest_production_product"] = max(
            product_performance,
            key=lambda item: item["production"],
        )

    # =====================================================
    # Model Extremes
    # =====================================================

    model_performance = kpis[
        "model_performance"
    ]

    if model_performance:

        kpis["highest_defect_model"] = max(
            model_performance,
            key=lambda item: item["defect_rate"],
        )

        kpis["highest_production_model"] = max(
            model_performance,
            key=lambda item: item["production"],
        )

    # =====================================================
    # Component Extremes
    # =====================================================

    component_performance = kpis[
        "component_performance"
    ]

    if component_performance:

        kpis["highest_defect_component"] = max(
            component_performance,
            key=lambda item: item["defect_rate"],
        )

    # =====================================================
    # Supplier Extremes
    # =====================================================

    supplier_performance = kpis[
        "supplier_performance"
    ]

    if supplier_performance:

        kpis["highest_defect_supplier"] = max(
            supplier_performance,
            key=lambda item: item["defect_rate"],
        )

    # =====================================================
    # Batch Extremes
    # =====================================================

    batch_performance = kpis[
        "batch_performance"
    ]

    if batch_performance:

        kpis["highest_defect_batch"] = max(
            batch_performance,
            key=lambda item: item["defect_rate"],
        )

    # =====================================================
    # Daily Production
    # =====================================================

    daily_production = []

    if date_column:

        temp_daily = dataframe.copy()

        temp_daily["_production"] = production
        temp_daily["_defects"] = defects
        temp_daily["_downtime"] = downtime

        temp_daily["_date"] = pd.to_datetime(
            temp_daily[date_column],
            errors="coerce",
        )

        temp_daily = temp_daily.dropna(
            subset=["_date"]
        )

        if not temp_daily.empty:

            grouped_daily = (
                temp_daily
                .groupby("_date")
                .agg(
                    production=(
                        "_production",
                        "sum",
                    ),
                    defects=(
                        "_defects",
                        "sum",
                    ),
                    downtime=(
                        "_downtime",
                        "sum",
                    ),
                )
                .reset_index()
            )

            grouped_daily["defect_rate"] = (
                grouped_daily["defects"]
                / grouped_daily["production"].replace(
                    0,
                    pd.NA,
                )
                * 100
            )

            grouped_daily["defect_rate"] = (
                grouped_daily["defect_rate"]
                .fillna(0)
            )

            grouped_daily = (
                grouped_daily
                .sort_values("_date")
            )

            for _, row in grouped_daily.iterrows():

                daily_production.append(
                    {
                        "date": row["_date"].strftime(
                            "%Y-%m-%d"
                        ),

                        "production": round(
                            float(row["production"]),
                            2,
                        ),

                        "defects": round(
                            float(row["defects"]),
                            2,
                        ),

                        "defect_rate": round(
                            float(row["defect_rate"]),
                            2,
                        ),

                        "downtime": round(
                            float(row["downtime"]),
                            2,
                        ),
                    }
                )

    kpis["daily_production"] = (
        daily_production
    )

    # =====================================================
    # Data Scope
    # =====================================================

    kpis["data_scope"] = {

        "has_product_data": bool(
            product_column
        ),

        "has_model_data": bool(
            model_column
        ),

        "has_component_data": bool(
            component_column
        ),

        "has_supplier_data": bool(
            supplier_column
        ),

        "has_batch_data": bool(
            batch_column
        ),

        "has_defect_type_data": bool(
            defect_type_column
        ),

        "has_quality_status_data": bool(
            quality_status_column
        ),

        "has_defect_data": bool(
            defect_column
        ),

        "has_downtime_data": bool(
            downtime_column
        ),

        "has_date_data": bool(
            date_column
        ),
    }

    kpis["data_quality"] = data_quality

    return kpis


# =========================================================
# AI Insights
# =========================================================

def generate_ai_insights(
    kpis: dict[str, Any],
) -> HardwareAnalysis:

    prompt = f"""
Dưới đây là kết quả KPI đã được Python tính toán
từ dữ liệu của một doanh nghiệp phần cứng.

DỮ LIỆU KPI:

{json.dumps(
    kpis,
    ensure_ascii=False,
    indent=2,
)}

Hãy tạo báo cáo Hardware Insights.

=========================================================
1. TITLE
=========================================================

Tạo tiêu đề ngắn gọn, phản ánh nội dung báo cáo.

Không dùng tên TextileAI.

Có thể sử dụng:

"Hardware Production Analysis"

hoặc tiêu đề tiếng Việt tương đương.

=========================================================
2. SUMMARY
=========================================================

Tóm tắt trực tiếp dựa trên dữ liệu.

Có thể đề cập:

- tổng sản lượng
- tổng số lỗi
- tỷ lệ lỗi tổng thể
- tổng downtime
- số sản phẩm
- số model
- số linh kiện
- số nhà cung cấp
- số batch
- số ngày

Không thêm số liệu ngoài dữ liệu Python.

=========================================================
3. KPIS
=========================================================

Chọn các KPI quan trọng nhất.

Có thể sử dụng:

- total_production
- total_defects
- defect_rate
- total_downtime
- average_production_per_record
- record_count
- product_count
- model_count
- component_count
- supplier_count
- batch_count
- date_count

Tên KPI phải phản ánh đúng bản chất.

Không thay đổi giá trị.

=========================================================
4. INSIGHTS
=========================================================

Phân tích các điểm dữ liệu đáng chú ý.

Có thể đề cập:

- sản phẩm có sản lượng cao nhất
- sản phẩm có tỷ lệ lỗi cao nhất
- model có sản lượng cao nhất
- model có tỷ lệ lỗi cao nhất
- linh kiện có tỷ lệ lỗi cao nhất
- nhà cung cấp có tỷ lệ lỗi cao nhất
- batch có tỷ lệ lỗi cao nhất
- loại lỗi xuất hiện nhiều nhất
- ngày có sản lượng cao nhất
- ngày có sản lượng thấp nhất
- khác biệt giữa sản phẩm
- khác biệt giữa model
- mối quan hệ giữa sản lượng, lỗi và downtime

Không gọi sản phẩm/model/nhà cung cấp là
"tốt nhất" nếu dữ liệu không đủ để kết luận.

Không gọi sản phẩm/model là "hiệu suất cao nhất"
nếu Python chưa tính KPI hiệu suất.

Sử dụng:

- "có sản lượng cao nhất"
- "có tỷ lệ lỗi cao nhất"
- "có tỷ lệ lỗi thấp nhất"
- "có downtime cao nhất"

Không biến tương quan thành nguyên nhân.

=========================================================
5. WARNINGS
=========================================================

Chỉ đưa ra cảnh báo dựa trên dữ liệu hoặc giới hạn dữ liệu.

Không tự đặt threshold.

Không sử dụng:

- "vượt chuẩn"
- "vượt mức cho phép"
- "nghiêm trọng"
- "khẩn cấp"
- "bất thường"
- "cao bất thường"
- "đáng kể"

nếu không có benchmark hoặc threshold.

Nếu dữ liệu chỉ bao phủ thời gian ngắn,
hãy nêu giới hạn.

Nếu không có nguyên nhân lỗi,
hãy nêu rằng chưa thể xác định root cause.

=========================================================
6. RECOMMENDATIONS
=========================================================

Đề xuất hành động thực tế:

- kiểm tra chất lượng sản phẩm
- kiểm tra linh kiện
- điều tra nguyên nhân lỗi
- kiểm tra batch có tỷ lệ lỗi cao
- rà soát dữ liệu nhà cung cấp
- đối chiếu chất lượng giữa nhà cung cấp
- kiểm tra quy trình sản xuất
- theo dõi model có tỷ lệ lỗi cao
- theo dõi downtime
- bổ sung dữ liệu nguyên nhân lỗi
- theo dõi KPI trong các ngày tiếp theo

Không khẳng định nguyên nhân nếu chưa có dữ liệu.

=========================================================
7. DATA LIMITATION
=========================================================

Nếu có giới hạn dữ liệu, phản ánh trong warnings.

Ví dụ:

- không có dữ liệu nguyên nhân lỗi
- không có benchmark
- không có mục tiêu sản lượng
- không có dữ liệu chi phí
- không có cycle time
- không có KPI hiệu suất
- dữ liệu chỉ bao phủ vài ngày

=========================================================
8. OUTPUT QUALITY
=========================================================

Ưu tiên:

- chính xác
- khách quan
- có số liệu
- dễ hiểu
- hữu ích cho quản lý
- phù hợp doanh nghiệp phần cứng

Không lặp lại cùng một ý tưởng.

Insights = điều dữ liệu cho thấy.

Warnings = điều cần chú ý hoặc giới hạn dữ liệu.

Recommendations = hành động nên xem xét.

Chỉ trả về JSON đúng schema.
"""

    interaction = get_client().interactions.create(
        model=GEMINI_MODEL,
        input=(
            SYSTEM_INSTRUCTION
            + "\n"
            + prompt
        ),
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": HardwareAnalysis.model_json_schema(),
        },
    )

    if not interaction.output_text:

        raise Exception(
            "Gemini không trả về Hardware Insights."
        )

    try:

        return HardwareAnalysis.model_validate_json(
            interaction.output_text
        )

    except Exception as error:

        raise Exception(
            "Gemini trả về Hardware Insights "
            f"không hợp lệ: {str(error)}"
        )


# =========================================================
# Main Function
# =========================================================

def analyze_hardware_dataframe(
    dataframe: pd.DataFrame,
) -> HardwareAnalysis:

    kpis = calculate_hardware_kpis(
        dataframe
    )

    return generate_ai_insights(
        kpis
    )