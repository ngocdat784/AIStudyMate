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


class ProductionAnalysis(BaseModel):
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

client = genai.Client(
    api_key=settings.GEMINI_API_KEY
)


SYSTEM_INSTRUCTION = """
You are TextileAI, an AI assistant specialized in
textile and garment manufacturing.

Your task is to analyze production KPIs calculated
by Python from CSV/XLSX production data.

=========================================================
IMPORTANT DATA RULES
=========================================================

- All numerical values supplied by Python are authoritative.
- Do NOT change, invent, estimate, or recalculate numerical values.
- Do NOT invent production data.
- Do NOT invent machines, dates, defects, downtime,
  causes, targets, standards, or thresholds.
- Only make conclusions that are supported by the supplied data.
- If the available data is insufficient, clearly state the limitation.

=========================================================
IMPORTANT ANALYSIS RULES
=========================================================

- Do NOT call a machine "hiệu suất cao nhất"
  or "hiệu quả nhất" unless an explicit efficiency KPI
  has been calculated by Python.

- Production quantity alone is NOT the same as efficiency.

- Do NOT claim that a machine has a specific root cause
  for defects or downtime unless the data contains
  evidence about that cause.

- Do NOT claim a trend unless the supplied data contains
  enough chronological information to support the trend.

- Do NOT claim that a problem is "nghiêm trọng",
  "khẩn cấp", or requires "ngay lập tức"
  unless there is an explicit threshold or rule
  supporting that conclusion.

- Do NOT use terms such as:
  "đáng kể",
  "bất thường",
  "cao bất thường",
  "vượt chuẩn",
  "vượt mức cho phép"
  unless the supplied data contains an explicit
  benchmark, threshold, or standard.

- When comparing machines, describe the actual metrics:
  production, defects, defect rate and downtime.

- Distinguish clearly between:
  1. facts from the data,
  2. analytical observations,
  3. recommended actions.

=========================================================
DEFECT RATE TERMINOLOGY
=========================================================

The Python-calculated "defect_rate" is:

    total defects / total production × 100

Therefore:

- Call it "tỷ lệ lỗi tổng thể"
  or "tỷ lệ lỗi chung".

- Do NOT call it "tỷ lệ lỗi trung bình"
  because it is not an arithmetic average of
  individual record defect rates.

=========================================================
MANUFACTURING FOCUS
=========================================================

Focus on:

- production output
- product quality
- defect rate
- machine performance
- downtime
- operational risks
- production consistency
- areas requiring investigation

Recommendations must be practical for textile
and garment manufacturing operations.

Recommendations should normally suggest:

- inspection
- monitoring
- root-cause investigation
- maintenance inspection
- process review
- operator/process comparison
- additional data collection

Do not state that a specific cause has been confirmed
when the data only shows a correlation.

=========================================================
LANGUAGE
=========================================================

- Use Vietnamese.
- Use concise and professional manufacturing terminology.
- Avoid generic motivational advice.
- Return only valid structured JSON.
"""


# =========================================================
# Column Detection
# =========================================================

COLUMN_ALIASES = {

    "machine": [
        "machine",
        "machine_id",
        "machine_name",
        "máy",
        "ma_may",
        "mã máy",
        "ten_may",
        "tên máy",
    ],

    "production": [
        "production",
        "output",
        "quantity",
        "qty",
        "production_qty",
        "output_qty",
        "sản lượng",
        "san_luong",
        "so_luong",
        "số lượng",
        "sl",
    ],

    "defects": [
        "defect",
        "defects",
        "defect_qty",
        "defect_quantity",
        "lỗi",
        "loi",
        "so_loi",
        "số lỗi",
        "loi_qty",
    ],

    "downtime": [
        "downtime",
        "downtime_minutes",
        "downtime_min",
        "idle_time",
        "minutes_down",
        "thời gian dừng",
        "thoi_gian_dung",
        "dừng máy",
        "dung_may",
    ],

    "date": [
        "date",
        "production_date",
        "work_date",
        "ngày",
        "ngay",
        "ngày sản xuất",
        "ngay_san_xuat",
    ],
}


def normalize_column_name(
    column: Any,
) -> str:

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

    # Exact match
    for alias in normalized_aliases:

        if alias in normalized_columns:
            return normalized_columns[alias]

    # Partial match
    for normalized, original in normalized_columns.items():

        for alias in normalized_aliases:

            if alias in normalized:
                return original

    return None


# =========================================================
# Data Cleaning
# =========================================================

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


# =========================================================
# KPI Calculation
# =========================================================

def calculate_production_kpis(
    dataframe: pd.DataFrame,
) -> dict[str, Any]:

    if dataframe.empty:
        raise ValueError(
            "File sản xuất không chứa dữ liệu."
        )

    dataframe = dataframe.copy()

    machine_column = find_column(
        dataframe,
        "machine",
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

    if production_column is None:
        raise ValueError(
            "Không tìm thấy cột sản lượng. "
            "Hãy sử dụng một trong các tên như "
            "'production', 'output', 'quantity' "
            "hoặc 'sản lượng'."
        )

    # -----------------------------------------------------
    # Numeric data
    # -----------------------------------------------------

    production = convert_numeric_column(
        dataframe,
        production_column,
    )

    total_production = float(
        production.sum()
    )

    # -----------------------------------------------------
    # Defects
    # -----------------------------------------------------

    total_defects = 0.0

    if defect_column:

        defects = convert_numeric_column(
            dataframe,
            defect_column,
        )

        total_defects = float(
            defects.sum()
        )

    else:

        defects = pd.Series(
            0,
            index=dataframe.index,
            dtype=float,
        )

    # -----------------------------------------------------
    # Downtime
    # -----------------------------------------------------

    total_downtime = 0.0

    if downtime_column:

        downtime = convert_numeric_column(
            dataframe,
            downtime_column,
        )

        total_downtime = float(
            downtime.sum()
        )

    else:

        downtime = pd.Series(
            0,
            index=dataframe.index,
            dtype=float,
        )

    # -----------------------------------------------------
    # Defect rate
    # -----------------------------------------------------

    defect_rate = 0.0

    if total_production > 0:

        defect_rate = (
            total_defects
            / total_production
            * 100
        )

    # -----------------------------------------------------
    # Average production per record
    # -----------------------------------------------------

    average_production_per_record = 0.0

    if len(dataframe) > 0:

        average_production_per_record = (
            total_production
            / len(dataframe)
        )

    # -----------------------------------------------------
    # Base KPIs
    # -----------------------------------------------------

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

        "machine_count": (
            int(
                dataframe[machine_column]
                .nunique()
            )
            if machine_column
            else 0
        ),

        "date_count": (
            int(
                dataframe[date_column]
                .nunique()
            )
            if date_column
            else 0
        ),
    }

    # =====================================================
    # Machine Performance
    # =====================================================

    machine_performance = []

    if machine_column:

        temp = dataframe.copy()

        temp["_production"] = production
        temp["_defects"] = defects
        temp["_downtime"] = downtime

        grouped = (
            temp
            .groupby(machine_column)
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
            / grouped["production"]
            .replace(0, pd.NA)
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

        for _, row in grouped.head(20).iterrows():

            machine_performance.append(
                {
                    "machine": str(
                        row[machine_column]
                    ),

                    "production": round(
                        float(
                            row["production"]
                        ),
                        2,
                    ),

                    "defects": round(
                        float(
                            row["defects"]
                        ),
                        2,
                    ),

                    "defect_rate": round(
                        float(
                            row["defect_rate"]
                        ),
                        2,
                    ),

                    "downtime": round(
                        float(
                            row["downtime"]
                        ),
                        2,
                    ),
                }
            )

    kpis["machine_performance"] = (
        machine_performance
    )

    # =====================================================
    # Machine Extremes
    # =====================================================

    if machine_performance:

        highest_defect_machine = max(
            machine_performance,
            key=lambda item: item["defect_rate"],
        )

        highest_downtime_machine = max(
            machine_performance,
            key=lambda item: item["downtime"],
        )

        highest_production_machine = max(
            machine_performance,
            key=lambda item: item["production"],
        )

        kpis[
            "highest_defect_machine"
        ] = highest_defect_machine

        kpis[
            "highest_downtime_machine"
        ] = highest_downtime_machine

        kpis[
            "highest_production_machine"
        ] = highest_production_machine

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
                / grouped_daily["production"]
                .replace(0, pd.NA)
                * 100
            )

            grouped_daily["defect_rate"] = (
                grouped_daily["defect_rate"]
                .fillna(0)
            )

            grouped_daily = grouped_daily.sort_values(
                "_date"
            )

            for _, row in grouped_daily.iterrows():

                daily_production.append(
                    {
                        "date": row["_date"].strftime(
                            "%Y-%m-%d"
                        ),

                        "production": round(
                            float(
                                row["production"]
                            ),
                            2,
                        ),

                        "defects": round(
                            float(
                                row["defects"]
                            ),
                            2,
                        ),

                        "defect_rate": round(
                            float(
                                row["defect_rate"]
                            ),
                            2,
                        ),

                        "downtime": round(
                            float(
                                row["downtime"]
                            ),
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
        "has_machine_data": bool(
            machine_column
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

    return kpis


# =========================================================
# AI Insights
# =========================================================

def generate_ai_insights(
    kpis: dict[str, Any],
) -> ProductionAnalysis:

    prompt = f"""
Dưới đây là kết quả KPI đã được Python tính toán
từ dữ liệu sản xuất của một doanh nghiệp dệt may.

DỮ LIỆU KPI:

{json.dumps(
    kpis,
    ensure_ascii=False,
    indent=2,
)}

Hãy tạo báo cáo AI Production Insights.

=========================================================
1. TITLE
=========================================================

Tạo tiêu đề ngắn gọn, phản ánh nội dung báo cáo.

=========================================================
2. SUMMARY
=========================================================

Tóm tắt tình hình sản xuất dựa trực tiếp trên dữ liệu.

Có thể đề cập:

- tổng sản lượng
- tổng lỗi
- tỷ lệ lỗi tổng thể
- downtime
- số máy
- số ngày

Không thêm số liệu ngoài dữ liệu Python.

Lưu ý:

"defect_rate" là tỷ lệ lỗi tổng thể:

tổng số lỗi / tổng sản lượng × 100

Không gọi chỉ số này là
"tỷ lệ lỗi trung bình".

=========================================================
3. KPIS
=========================================================

Chọn các KPI quan trọng nhất để hiển thị.

Có thể sử dụng:

- total_production
- total_defects
- defect_rate
- total_downtime
- average_production_per_record
- record_count
- machine_count
- date_count

Nếu sử dụng machine_performance hoặc
daily_production thì chỉ dùng số liệu đã có.

Không được thay đổi giá trị.

Tên KPI phải phản ánh đúng bản chất.

Ví dụ:

- "Tổng sản lượng"
- "Tổng số lỗi"
- "Tỷ lệ lỗi tổng thể"
- "Tổng thời gian dừng máy"
- "Số lượng máy"
- "Số ngày ghi nhận"

=========================================================
4. INSIGHTS
=========================================================

Phân tích những điểm đáng chú ý.

Có thể đề cập:

- Máy nào có sản lượng cao nhất.
- Máy nào có tỷ lệ lỗi cao nhất.
- Máy nào có downtime cao nhất.
- Máy nào có tỷ lệ lỗi thấp nhất.
- Sự khác biệt giữa các máy.
- Ngày nào có sản lượng cao/thấp nếu dữ liệu ngày đủ rõ.
- Mối quan hệ đáng chú ý giữa sản lượng,
  lỗi và downtime.

QUAN TRỌNG:

Không được gọi một máy là
"hiệu suất cao nhất"
nếu Python chưa tính KPI hiệu suất.

Thay vào đó hãy nói chính xác:

- "có sản lượng cao nhất"
- "có tỷ lệ lỗi thấp nhất"
- "có downtime thấp nhất"

Không được biến một mối tương quan thành nguyên nhân
đã được xác nhận.

Ví dụ:

KHÔNG nên viết:

"Máy M03 có vấn đề do hệ thống máy bị hỏng."

Nên viết:

"Máy M03 có downtime và tỷ lệ lỗi cao nhất
trong dữ liệu được ghi nhận."

=========================================================
5. WARNINGS
=========================================================

Chỉ đưa ra cảnh báo khi dữ liệu cho thấy
một điểm đáng chú ý.

Không tự đặt ngưỡng tiêu chuẩn.

Không sử dụng các từ hoặc cụm từ:

- "vượt chuẩn"
- "vượt mức cho phép"
- "nghiêm trọng"
- "khẩn cấp"
- "bất thường"
- "cao bất thường"
- "đáng kể"

nếu dữ liệu không cung cấp benchmark,
threshold hoặc tiêu chuẩn tương ứng.

Thay vào đó hãy mô tả trực tiếp số liệu.

Ví dụ:

KHÔNG nên viết:

"Máy M03 có downtime cao đáng kể."

Nên viết:

"Máy M03 có downtime 215 phút,
cao nhất trong 3 máy được ghi nhận."

Nếu dữ liệu chỉ bao phủ một khoảng thời gian ngắn,
hãy nêu rõ giới hạn.

Ví dụ:

"Dữ liệu hiện chỉ bao phủ 3 ngày nên chưa đủ
để xác định xu hướng dài hạn."

=========================================================
6. RECOMMENDATIONS
=========================================================

Đề xuất hành động thực tế cho môi trường
sản xuất dệt may.

Có thể đề xuất:

- kiểm tra máy có downtime cao
- điều tra nguyên nhân lỗi
- kiểm tra quy trình vận hành
- kiểm tra thông số máy
- đối chiếu quy trình giữa các máy
- tăng cường thu thập dữ liệu
- theo dõi các KPI trong những ngày tiếp theo

Không được khẳng định nguyên nhân khi chưa có dữ liệu.

Ví dụ KHÔNG nên viết:

"Máy M03 bị hỏng hệ thống làm mát."

Nếu dữ liệu không chứa thông tin về hệ thống
làm mát thì không được kết luận như vậy.

Không nên tự động kết luận rằng máy cần
"bảo dưỡng ngay lập tức".

Nên viết:

"Cần kiểm tra kỹ thuật và điều tra nguyên nhân
khiến máy M03 có downtime và tỷ lệ lỗi cao."

Các recommendation phải xuất phát từ dữ liệu
và không được tự tạo nguyên nhân.

=========================================================
7. DATA LIMITATION
=========================================================

Nếu dữ liệu có giới hạn, hãy phản ánh giới hạn đó
trong warnings hoặc insights.

Ví dụ:

- dữ liệu chỉ có vài ngày
- không có thông tin nguyên nhân downtime
- không có KPI hiệu suất
- không có mục tiêu sản lượng
- không có tiêu chuẩn tỷ lệ lỗi

Không được suy diễn vượt quá dữ liệu.

=========================================================
8. OUTPUT QUALITY
=========================================================

Ưu tiên:

- chính xác
- khách quan
- có số liệu
- dễ hiểu
- hữu ích cho người quản lý sản xuất

Không lặp lại cùng một ý tưởng quá nhiều lần
giữa insights, warnings và recommendations.

Insights = điều dữ liệu cho thấy.

Warnings = điều cần chú ý hoặc giới hạn dữ liệu.

Recommendations = hành động nên xem xét.

=========================================================

Chỉ trả về JSON đúng với schema.
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=(
            SYSTEM_INSTRUCTION
            + "\n"
            + prompt
        ),
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": ProductionAnalysis.model_json_schema(),
        },
    )

    if not interaction.output_text:

        raise Exception(
            "Gemini không trả về Production Insights."
        )

    try:

        return ProductionAnalysis.model_validate_json(
            interaction.output_text
        )

    except Exception as e:

        raise Exception(
            "Gemini trả về Production Insights "
            f"không hợp lệ: {str(e)}"
        )


# =========================================================
# Main Function
# =========================================================

def analyze_production_dataframe(
    dataframe: pd.DataFrame,
) -> ProductionAnalysis:

    kpis = calculate_production_kpis(
        dataframe
    )

    return generate_ai_insights(
        kpis
    )