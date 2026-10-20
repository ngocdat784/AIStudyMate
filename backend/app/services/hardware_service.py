from __future__ import annotations

import json
import math
import re
from typing import Any

import pandas as pd
from google import genai
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.data_quality_service import (
    _to_numeric,
    calculate_data_quality,
)
from app.services.anomaly_detection_service import (
    detect_hardware_anomalies,
)
from app.services.gemini_retry import call_gemini_with_retry
from app.services.hardware_ai_service import (
    _gemini_response_schema,
    analyze_hardware_deep,
    normalize_summary_record_scope,
)


GEMINI_MODEL = "gemini-3.5-flash-lite"


# =========================================================
# Response Models
# =========================================================

class KPI(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | int | str
    unit: str
    description: str


class HardwareAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


AI_UNAVAILABLE_MESSAGE = (
    "Dịch vụ AI hiện tạm thời không khả dụng. "
    "Các chỉ số được tính bằng Python vẫn có thể sử dụng."
)


def is_temporary_ai_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "code", None)
    try:
        if int(status_code) in {408, 429, 500, 502, 503, 504}:
            return True
    except (TypeError, ValueError):
        pass

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "408",
            "503",
            "service_unavailable",
            "high demand",
            "429",
            "resource_exhausted",
            "500",
            "502",
            "504",
        )
    )


def build_fallback_report(kpis: dict[str, Any]) -> dict[str, Any]:
    metric_definitions = {
        "total_production": (
            "Tổng sản lượng",
            "sản phẩm",
            "Tổng sản lượng ghi nhận từ các bản ghi được sử dụng để tính KPI.",
        ),
        "total_defects": (
            "Tổng số lỗi",
            "lỗi",
            "Tổng số lỗi ghi nhận từ các bản ghi được sử dụng để tính KPI.",
        ),
        "defect_rate": (
            "Tỷ lệ lỗi tổng thể",
            "%",
            "Tỷ lệ lỗi tổng thể do Python tính toán.",
        ),
        "total_downtime": (
            "Tổng downtime",
            "phút",
            "Tổng thời gian ngừng máy ghi nhận được, tính bằng phút.",
        ),
        "average_production_per_record": (
            "Sản lượng trung bình mỗi bản ghi",
            "sản phẩm/bản ghi",
            "Giá trị do Python tính toán trên các bản ghi được sử dụng.",
        ),
        "record_count": (
            "Số bản ghi",
            "bản ghi",
            "Số bản ghi được sử dụng để tính KPI.",
        ),
        "product_count": ("Số sản phẩm", "sản phẩm", "Số sản phẩm trong dữ liệu."),
        "model_count": ("Số model", "model", "Số model trong dữ liệu."),
        "component_count": (
            "Số loại linh kiện xác định",
            "loại",
            "Số lượng loại linh kiện xác định, không bao gồm nhóm chưa xác định.",
        ),
        "supplier_count": (
            "Số nhà cung cấp",
            "nhà cung cấp",
            "Số nhà cung cấp trong dữ liệu.",
        ),
        "batch_count": ("Số batch", "batch", "Số batch trong dữ liệu."),
        "date_count": ("Số ngày", "ngày", "Số ngày có dữ liệu."),
    }
    fallback_kpis = []

    for key, (name, unit, description) in metric_definitions.items():
        value = kpis.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            fallback_kpis.append({
                "name": name,
                "value": value,
                "unit": unit,
                "description": description,
            })

    return {
        "title": "Báo cáo phân tích phần cứng",
        "summary": (
            "Các chỉ số trong báo cáo được tính bằng Python. "
            "Phần diễn giải do AI tạo hiện chưa khả dụng."
        ),
        "kpis": fallback_kpis,
        "insights": [
            "Sử dụng các KPI và kết quả anomaly_analysis do Python cung cấp; "
            "chưa có nhận xét do AI tạo."
        ],
        "warnings": [AI_UNAVAILABLE_MESSAGE],
        "recommendations": [
            "Đối chiếu các KPI và phát hiện bất thường do Python tính toán; "
            "thử tạo lại phần phân tích AI sau."
        ],
    }


def build_fallback_deep_analysis() -> dict[str, Any]:
    unavailable_section = {
        "summary": "Phần phân tích chuyên sâu do AI tạo hiện chưa khả dụng.",
        "key_observations": [],
    }
    return {
        "executive_summary": dict(unavailable_section),
        "production_analysis": dict(unavailable_section),
        "quality_analysis": dict(unavailable_section),
        "component_analysis": dict(unavailable_section),
        "supplier_analysis": dict(unavailable_section),
        "batch_analysis": dict(unavailable_section),
        "downtime_analysis": dict(unavailable_section),
        "risk_areas": [],
        "recommended_actions": [],
        "ai_status": "temporarily_unavailable",
    }


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
- The supplied "defect_rate" values are percentages. Whenever a
  defect rate appears in prose, include "%" (for example, 2.05 means
  2.05%, not a unitless value). Preserve numeric KPI values in JSON.
- All supplied downtime values are in minutes. Keep every total,
  product/model/component/supplier/batch and daily downtime value in
  minutes; do not relabel as hours or convert without dividing by 60.
- "component_count" counts only identified component types. Exclude the
  "Không xác định" group from this count, while still describing its
  production in component analysis when present.
- data_quality.valid_rows/invalid_rows evaluate completeness and validity
  of all required fields on the original data, including component fields.
  data_processing.rows_used_for_kpi counts deduplicated rows with valid
  production and valid defects/downtime when those columns are present.
  A row missing Component may still be used for KPI calculations, so
  these counts can differ; explain them separately and never treat
  invalid rows as defective products.
- Python calculates all KPI totals, overall defect rate, downtime, group
  rankings, and record counts. Treat those values as authoritative; do
  not independently recompute or state conflicting numbers.
- Python records excluded KPI rows by metric in
  data_processing.invalid_metric_rows_excluded_by_metric. Those counts
  may overlap when one row has multiple invalid metrics. Do not invent
  exclusion counts or reasons.
- In the summary, describe data_processing.rows_used_for_kpi as records
  "được sử dụng để tính KPI", never as records that are universally
  "hợp lệ". If reporting the date count, say they span that many
  production days. Example: "Dữ liệu bao gồm 12 bản ghi được sử dụng để
  tính KPI, trải dài trong 5 ngày sản xuất."
- In defect_type_analysis, "Không xác định" aggregates the defects
  quantities for records whose defect type is missing/blank. Its
  "defects" value is a defect quantity, not a count of rows, invalid
  records, or records excluded from KPI calculation. Never infer these
  counts are equal.
- Aggregated defect rates for components, models and batches are
  descriptive associations only. Do not claim they cause defects or
  prove their processes are faulty; recommend them for inspection only.

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
Every defect_rate value is expressed as a percentage. Include the "%"
symbol whenever stating a defect rate in summaries, insights, warnings,
or recommendations. For example, say "2,05%" rather than "2.05".

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
    dataframe = dataframe.dropna(how="all")

    # Remove rows where every value is an empty string.
    empty_rows = dataframe.apply(
        lambda row: all(
            pd.isna(value) or str(value).strip() == ""
            for value in row
        ),
        axis=1,
    )

    dataframe = dataframe.loc[~empty_rows].copy()

    if dataframe.empty:
        raise ValueError(
            "File dữ liệu phần cứng không chứa dữ liệu hợp lệ."
        )

    # Remove completely duplicated records.
    dataframe = dataframe.drop_duplicates(
        keep="first"
    )

    dataframe = dataframe.reset_index(drop=True)

    if dataframe.empty:
        raise ValueError(
            "Không còn dữ liệu sau khi làm sạch."
        )

    return dataframe


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


def count_identified_components(
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
    unidentified_values = {
        "",
        "không xác định",
        "khong xac dinh",
        "unknown",
    }
    values = values[
        ~values.str.casefold().isin(unidentified_values)
    ]

    return int(values.nunique())


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

    if dataframe.empty:
        raise ValueError(
            "File dữ liệu phần cứng không chứa dữ liệu."
        )

    # 1. Kiểm định dữ liệu gốc trước khi làm sạch.
    data_quality = calculate_data_quality(
        dataframe
    )

    # 2. Tiếp tục quy trình xử lý hiện tại.
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

    # =====================================================
    # Parse numeric fields and filter invalid KPI records
    # =====================================================

    # Production is required for production KPIs.
    production_raw = _to_numeric(
        dataframe[production_column]
    )

    if defect_column:
        defects_raw = _to_numeric(
            dataframe[defect_column]
        )
    else:
        defects_raw = pd.Series(
            0.0,
            index=dataframe.index,
        )

    if downtime_column:
        downtime_raw = _to_numeric(
            dataframe[downtime_column]
        )
    else:
        downtime_raw = pd.Series(
            0.0,
            index=dataframe.index,
        )

    excluded_rows_by_metric = {}
    for metric_name, raw_values, column_exists in (
        ("production", production_raw, True),
        ("defects", defects_raw, bool(defect_column)),
        ("downtime", downtime_raw, bool(downtime_column)),
    ):
        if column_exists:
            excluded_rows_by_metric[metric_name] = int(
                (raw_values.isna() | raw_values.lt(0)).sum()
            )

    valid_mask = (
        production_raw.notna()
        & production_raw.ge(0)
    )

    if defect_column:
        valid_mask &= (
            defects_raw.notna()
            & defects_raw.ge(0)
        )

    if downtime_column:
        valid_mask &= (
            downtime_raw.notna()
            & downtime_raw.ge(0)
        )

    rows_after_deduplication = len(dataframe)
    dataframe = dataframe.loc[valid_mask].copy()

    production = production_raw.loc[valid_mask].astype(float)
    defects = defects_raw.loc[valid_mask].astype(float)
    downtime = downtime_raw.loc[valid_mask].astype(float)

    data_processing = {
        "rows_after_deduplication": rows_after_deduplication,
        "rows_used_for_kpi": len(dataframe),
        "invalid_metric_rows_excluded": (
            rows_after_deduplication - len(dataframe)
        ),
        "invalid_metric_rows_excluded_by_metric": (
            excluded_rows_by_metric
        ),
    }

    if dataframe.empty:
        raise ValueError(
            "Không còn bản ghi hợp lệ để tính KPI. "
            "Vui lòng kiểm tra Production, Defects và Downtime."
        )

    # =====================================================
    # KPI Totals
    # =====================================================

    total_production = float(production.sum())
    total_defects = float(defects.sum())
    total_downtime = float(downtime.sum())

    defect_rate = (
        total_defects / total_production * 100
        if total_production > 0
        else 0.0
    )

    average_production_per_record = (
        total_production / len(dataframe)
        if len(dataframe) > 0
        else 0.0
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

        "component_count": count_identified_components(
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
    kpis["data_processing"] = data_processing

    return kpis


def compare_hardware_models(
    dataframe: pd.DataFrame,
    model_a: str,
    model_b: str,
) -> dict[str, Any]:
    if not model_a.strip() or not model_b.strip():
        raise ValueError("Vui lòng chọn đủ hai model để so sánh.")

    if model_a == model_b:
        raise ValueError("Hai model được chọn phải khác nhau.")

    cleaned_dataframe = clean_dataframe(dataframe)
    model_column = find_column(cleaned_dataframe, "model")
    if model_column is None:
        raise ValueError("Không tìm thấy cột model trong dữ liệu.")

    model_values = cleaned_dataframe[model_column].astype("string")
    model_reports: dict[str, dict[str, Any]] = {}

    for label, model_name in (("model_a", model_a), ("model_b", model_b)):
        model_dataframe = cleaned_dataframe.loc[
            model_values.eq(model_name).fillna(False)
        ].copy()
        if model_dataframe.empty:
            raise ValueError(
                f"Không tìm thấy model '{model_name}' trong dữ liệu."
            )

        try:
            kpis = calculate_hardware_kpis(model_dataframe)
        except ValueError as error:
            raise ValueError(
                f"Model '{model_name}' không có đủ dữ liệu hợp lệ để tính KPI: "
                f"{error}"
            ) from error

        production = kpis["total_production"]
        defects = kpis["total_defects"]
        model_reports[label] = {
            "name": model_name,
            "production": production,
            "defects": defects,
            "defect_rate": (
                kpis["defect_rate"]
                if production > 0
                else None
            ),
            "downtime": kpis["total_downtime"],
            "rows_used_for_kpi": kpis["data_processing"]["rows_used_for_kpi"],
            "invalid_rows_excluded": (
                kpis["data_processing"]["invalid_metric_rows_excluded"]
            ),
        }

    metrics = ("production", "defects", "downtime")
    differences: dict[str, float | None] = {
        metric: round(
            model_reports["model_b"][metric]
            - model_reports["model_a"][metric],
            2,
        )
        for metric in metrics
    }
    rate_a = model_reports["model_a"]["defect_rate"]
    rate_b = model_reports["model_b"]["defect_rate"]
    rate_is_available = rate_a is not None and rate_b is not None
    differences["defect_rate_percentage_points"] = (
        round(rate_b - rate_a, 2)
        if rate_is_available
        else None
    )

    higher_defect_rate_model = None
    if rate_is_available and rate_a != rate_b:
        higher_defect_rate_model = (
            model_a if rate_a > rate_b else model_b
        )

    return {
        "models": model_reports,
        "differences": differences,
        "rate_comparison_status": (
            "available" if rate_is_available else "insufficient_production"
        ),
        "higher_defect_rate_model": higher_defect_rate_model,
    }


# =========================================================
# AI Insights
# =========================================================

def _normalize_generated_kpis(
    analysis: HardwareAnalysis,
    source_kpis: dict[str, Any],
) -> HardwareAnalysis:
    for kpi in analysis.kpis:
        name = kpi.name.strip().casefold()

        if name in {
            "total_production",
            "total production",
            "tổng sản lượng",
        }:
            if "total_production" in source_kpis:
                kpi.value = source_kpis["total_production"]
            kpi.name = "Tổng sản lượng"
            kpi.description = (
                "Tổng sản lượng ghi nhận từ các bản ghi được sử dụng "
                "để tính KPI."
            )

        if name in {
            "total_defects",
            "total defects",
            "tổng số lỗi",
            "tổng lỗi",
        }:
            if "total_defects" in source_kpis:
                kpi.value = source_kpis["total_defects"]
            kpi.name = "Tổng số lỗi"
            kpi.description = (
                "Tổng số lỗi ghi nhận từ các bản ghi được sử dụng "
                "để tính KPI."
            )

        is_downtime = (
            "downtime" in name
            or "thời gian dừng" in name
            or "thời gian ngừng" in name
            or "dừng máy" in name
            or "ngừng máy" in name
        )
        is_total_downtime = is_downtime and (
            name in {
                "total_downtime",
                "tổng downtime",
                "tổng thời gian dừng máy",
                "tổng thời gian ngừng máy",
            }
            or "tổng" in name
            or "total" in name
        )
        if is_downtime:
            kpi.unit = "phút"
            if is_total_downtime:
                kpi.value = source_kpis["total_downtime"]
                kpi.name = "Tổng downtime"
                kpi.description = (
                    "Tổng thời gian ngừng máy ghi nhận được, "
                    "tính bằng phút."
                )
            else:
                kpi.description = (
                    "Thời gian ngừng máy ghi nhận được, tính bằng phút."
                )

        if (
            "defect_rate" in name
            or "defect rate" in name
            or "tỷ lệ lỗi" in name
        ):
            kpi.unit = "%"
            if name in {
                "defect_rate",
                "defect rate",
                "tỷ lệ lỗi tổng thể",
                "tổng tỷ lệ lỗi",
            }:
                if "defect_rate" in source_kpis:
                    kpi.value = source_kpis["defect_rate"]
                kpi.name = "Tỷ lệ lỗi tổng thể"
                kpi.description = (
                    "Tỷ lệ lỗi tổng thể từ các bản ghi được sử dụng "
                    "để tính KPI, tính bằng phần trăm."
                )

        if name in {
            "component_count",
            "số loại linh kiện xác định",
            "số lượng loại linh kiện xác định",
            "số lượng linh kiện xác định",
        }:
            kpi.value = source_kpis["component_count"]
            kpi.unit = "loại"
            kpi.name = "Số loại linh kiện xác định"
            kpi.description = (
                "Số lượng loại linh kiện xác định được trong dữ liệu, "
                "không bao gồm nhóm chưa xác định."
            )

    return analysis


def generate_ai_insights(
    kpis: dict[str, Any],
    anomalies: dict[str, Any] | None = None,
) -> HardwareAnalysis:

    if anomalies is None:
        anomalies = {
            "status": "insufficient_data",
            "findings": {},
        }
    context = {
        "kpis": kpis,
        "anomaly_analysis": anomalies,
    }
    context_json = json.dumps(
        context,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )

    prompt = f"""
Dưới đây là kết quả KPI đã được Python tính toán
từ dữ liệu của một doanh nghiệp phần cứng.

DỮ LIỆU ĐẦU VÀO:

{context_json}

QUY TẮC PHÂN TÍCH BẤT THƯỜNG

1. anomaly_analysis là kết quả do Python tính toán theo quy tắc
   xác định. Không tự tính lại, thay đổi hoặc phủ nhận các kết quả này.

2. Chỉ giải thích các phát hiện được cung cấp trong
   anomaly_analysis. Không tự tạo thêm batch, model, ngày,
   tỷ lệ lỗi, ngưỡng hoặc số liệu.

3. Phân biệt:
   - Giá trị cao nhất/thấp nhất: kết quả xếp hạng.
   - Ngoại lệ thống kê: kết quả vượt quy tắc phát hiện cụ thể.
   - Chênh lệch lớn: kết quả so sánh với ngưỡng đã cấu hình.

4. Nếu unusual_batch_defect_rate.detected là false,
   không được tuyên bố rằng có batch bất thường theo quy tắc IQR,
   kể cả khi một batch có tỷ lệ lỗi cao nhất.

5. Nếu model_quality_difference.detected là false,
   không được tuyên bố độ chênh lệch giữa model vượt ngưỡng
   cấu hình. Vẫn có thể mô tả số liệu so sánh nếu được cung cấp.

6. Không suy luận tương quan thành quan hệ nhân quả.
   Không kết luận nhà cung cấp, linh kiện hoặc model là nguyên nhân
   gây lỗi nếu dữ liệu không chứng minh được điều đó.

7. Mỗi nhận định cần gắn với số liệu hoặc phát hiện cụ thể.
   Nếu dữ liệu không đủ, phải nói rõ chưa đủ bằng chứng.

8. Đề xuất hành động theo hướng kiểm tra, đối chiếu và thu thập thêm
   bằng chứng; không khẳng định nguyên nhân gốc rễ chưa được xác minh.

9. Không coi điểm chất lượng dữ liệu là tỷ lệ lỗi sản phẩm.
   Không cộng các bản ghi bị loại trở lại KPI.

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

For total_downtime, use the name "Tổng downtime", preserve its numeric
value, use unit "phút", and use the description
"Tổng thời gian ngừng máy ghi nhận được, tính bằng phút."
For total_production, use the Python-calculated value and the description
"Tổng sản lượng ghi nhận từ các bản ghi được sử dụng để tính KPI."
For total_defects, use the Python-calculated value and describe it as
defects recorded in rows used to calculate KPIs.
For component_count, use the name "Số loại linh kiện xác định", preserve
the Python-calculated count, use unit "loại", and describe it as
"Số lượng loại linh kiện xác định được trong dữ liệu, không bao gồm nhóm chưa xác định."
If including a defect_rate KPI, use unit "%".

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
Every defect rate quoted in prose must include the "%" symbol. Use
Vietnamese decimal commas in prose (for example, 2.05 becomes 2,05%),
without changing numeric values in structured KPI fields.

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
Priority sẽ được chuẩn hóa bằng Python theo các ngưỡng cấu hình ở trên.
Không tự đặt ngưỡng; không dùng critical nếu không có quy tắc cấu hình.

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

    interaction = call_gemini_with_retry(
        lambda: get_client().interactions.create(
            model=GEMINI_MODEL,
            input=(
                SYSTEM_INSTRUCTION
                + "\n"
                + prompt
            ),
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _gemini_response_schema(HardwareAnalysis),
            },
        ),
    )

    if not interaction.output_text:

        raise Exception(
            "Gemini không trả về Hardware Insights."
        )

    try:

        analysis = HardwareAnalysis.model_validate_json(
            interaction.output_text
        )
        analysis.summary = normalize_summary_record_scope(
            analysis.summary,
            kpis,
        )
        analysis = _normalize_generated_kpis(
            analysis,
            kpis,
        )
        return analysis

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
) -> dict[str, Any]:

    kpis = calculate_hardware_kpis(
        dataframe
    )

    anomalies = detect_hardware_anomalies(kpis)

    ai_unavailable = False
    insights_available = True

    try:
        analysis = generate_ai_insights(
            kpis,
            anomalies=anomalies,
        )
        result = analysis.model_dump()
    except Exception as exc:
        if not is_temporary_ai_error(exc):
            raise
        insights_available = False
        ai_unavailable = True
        result = build_fallback_report(kpis)

    for section in (
        "product_performance",
        "model_performance",
        "component_performance",
        "supplier_performance",
        "batch_performance",
        "defect_type_analysis",
        "daily_production",
        "data_scope",
        "data_quality",
        "data_processing",
    ):
        result[section] = kpis.get(
            section,
            {} if section in (
                "data_scope",
                "data_quality",
                "data_processing",
            ) else [],
        )

    result["anomaly_analysis"] = anomalies

    deep_available = True
    try:
        result["ai_analysis"] = analyze_hardware_deep(
            kpis,
            anomalies=anomalies,
        )
    except Exception as exc:
        if not is_temporary_ai_error(exc):
            raise
        deep_available = False
        ai_unavailable = True
        result["ai_analysis"] = build_fallback_deep_analysis()

    if ai_unavailable:
        result["ai_status"] = (
            "partially_unavailable"
            if insights_available or deep_available
            else "temporarily_unavailable"
        )
        result["ai_error"] = AI_UNAVAILABLE_MESSAGE
    else:
        result["ai_status"] = "available"

    return result