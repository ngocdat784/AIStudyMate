
# app/services/anomaly_detection_service.py

from __future__ import annotations

from typing import Any
import math


# ============================================================
# CONFIGURATION
# ============================================================

# Ngưỡng chênh lệch tỷ lệ lỗi giữa các model, đơn vị điểm phần trăm.
# Đây là ngưỡng kỹ thuật ban đầu, cần điều chỉnh theo nghiệp vụ thực tế.
MODEL_RATE_SPREAD_THRESHOLD_PP = 0.30

# Hệ số IQR để xác định tỷ lệ lỗi batch nằm ngoài phân bố thông thường.
BATCH_IQR_FACTOR = 1.5


# ============================================================
# NORMALIZATION HELPERS
# ============================================================

GROUP_CONFIG = {
    "product": {
        "field": "product_performance",
        "label_field": "product",
    },
    "model": {
        "field": "model_performance",
        "label_field": "model",
    },
    "component": {
        "field": "component_performance",
        "label_field": "component",
    },
    "supplier": {
        "field": "supplier_performance",
        "label_field": "supplier",
    },
    "batch": {
        "field": "batch_performance",
        "label_field": "batch",
    },
}


def _to_dict(value: Any) -> dict:
    """Chuyển Pydantic model hoặc mapping về dict."""
    if isinstance(value, dict):
        return value

    if hasattr(value, "model_dump"):
        return value.model_dump()

    return {}


def _to_rows(value: Any) -> list[dict]:
    """Chuẩn hóa danh sách các bản ghi KPI."""
    if not isinstance(value, list):
        return []

    return [
        row if isinstance(row, dict) else _to_dict(row)
        for row in value
        if isinstance(row, dict) or hasattr(row, "model_dump")
    ]


def _number(value: Any) -> float | None:
    """Chấp nhận số hữu hạn; bỏ qua dữ liệu thiếu hoặc không hợp lệ."""
    if value is None or isinstance(value, bool):
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None

    return result


def _get_label(row: dict, label_field: str) -> str:
    """Lấy tên nhóm mà không tự suy đoán danh tính."""
    value = row.get(label_field)

    if value is None:
        value = row.get("name")

    if value is None:
        value = row.get("label")

    if value is None or not str(value).strip():
        return "Không xác định"

    return str(value).strip()


def _valid_metric_rows(
    rows: list[dict],
    label_field: str,
    metric: str,
    *,
    positive_only: bool = False,
) -> list[dict]:
    """Chỉ giữ các dòng có metric số hợp lệ."""
    result = []

    for row in rows:
        value = _number(row.get(metric))

        if value is None:
            continue

        if positive_only and value <= 0:
            continue

        item = dict(row)
        item["_metric_value"] = value
        item["_label"] = _get_label(row, label_field)
        result.append(item)

    return result


# ============================================================
# EXTREME VALUE DETECTION
# ============================================================

def _find_extreme(
    rows: list[dict],
    label_field: str,
    metric: str,
    *,
    mode: str,
    positive_only: bool = False,
) -> dict | None:
    valid_rows = _valid_metric_rows(
        rows,
        label_field,
        metric,
        positive_only=positive_only,
    )

    if not valid_rows:
        return None

    if mode == "max":
        selected = max(valid_rows, key=lambda row: row["_metric_value"])
    else:
        selected = min(valid_rows, key=lambda row: row["_metric_value"])

    return {
        "entity": selected["_label"],
        "metric": metric,
        "value": selected["_metric_value"],
        "unit": (
            "percent"
            if metric == "defect_rate"
            else "minutes"
            if metric == "downtime"
            else "units"
        ),
        "method": "extreme_value",
        "comparison_count": len(valid_rows),
    }


def _analyze_group_extremes(kpis: dict) -> dict:
    result = {
        "highest_defect_rate": {},
        "highest_downtime": {},
        "lowest_production": {},
    }

    for group_name, config in GROUP_CONFIG.items():
        rows = _to_rows(kpis.get(config["field"]))
        label_field = config["label_field"]

        highest_rate = _find_extreme(
            rows,
            label_field,
            "defect_rate",
            mode="max",
        )

        highest_downtime = _find_extreme(
            rows,
            label_field,
            "downtime",
            mode="max",
        )

        lowest_production = _find_extreme(
            rows,
            label_field,
            "production",
            mode="min",
            positive_only=True,
        )

        if highest_rate is not None:
            result["highest_defect_rate"][group_name] = highest_rate

        if highest_downtime is not None:
            result["highest_downtime"][group_name] = highest_downtime

        if lowest_production is not None:
            result["lowest_production"][group_name] = lowest_production

    return result


# ============================================================
# DAILY PRODUCTION CHANGE
# ============================================================

def _analyze_daily_change(kpis: dict) -> dict:
    rows = _to_rows(kpis.get("daily_production"))
    daily = []

    for row in rows:
        production = _number(row.get("production"))
        date = row.get("date")

        if production is None or date is None:
            continue

        daily.append({
            "date": str(date),
            "production": production,
        })

    daily.sort(key=lambda row: row["date"])

    if len(daily) < 2:
        return {
            "detected": False,
            "reason": "Cần ít nhất hai ngày có dữ liệu hợp lệ.",
            "method": "largest_absolute_day_to_day_change",
        }

    changes = []

    for previous, current in zip(daily, daily[1:]):
        absolute_change = abs(
            current["production"] - previous["production"]
        )

        previous_production = previous["production"]

        percent_change = (
            (current["production"] - previous_production)
            / abs(previous_production)
            * 100
            if previous_production != 0
            else None
        )

        changes.append({
            "from_date": previous["date"],
            "to_date": current["date"],
            "previous_production": previous_production,
            "current_production": current["production"],
            "change": current["production"] - previous_production,
            "absolute_change": absolute_change,
            "percent_change": percent_change,
            "direction": (
                "increase"
                if current["production"] > previous_production
                else "decrease"
                if current["production"] < previous_production
                else "unchanged"
            ),
        })

    largest = max(changes, key=lambda item: item["absolute_change"])

    return {
        "detected": True,
        "method": "largest_absolute_day_to_day_change",
        "unit": "units",
        **largest,
        "all_day_to_day_changes": changes,
    }


# ============================================================
# BATCH OUTLIER DETECTION — IQR
# ============================================================

def _quantile(sorted_values: list[float], q: float) -> float:
    """
    Linear interpolation quantile.
    Input must be sorted and non-empty.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (len(sorted_values) - 1) * q
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return sorted_values[lower_index]

    fraction = position - lower_index

    return (
        sorted_values[lower_index] * (1 - fraction)
        + sorted_values[upper_index] * fraction
    )


def _analyze_batch_outliers(kpis: dict) -> dict:
    config = GROUP_CONFIG["batch"]
    rows = _to_rows(kpis.get(config["field"]))

    valid_rows = _valid_metric_rows(
        rows,
        config["label_field"],
        "defect_rate",
    )

    if len(valid_rows) < 4:
        return {
            "method": "IQR",
            "detected": False,
            "outliers": [],
            "reason": "Không đủ ít nhất 4 batch hợp lệ để áp dụng IQR.",
        }

    rates = sorted(row["_metric_value"] for row in valid_rows)

    q1 = _quantile(rates, 0.25)
    q3 = _quantile(rates, 0.75)
    iqr = q3 - q1

    lower_fence = q1 - BATCH_IQR_FACTOR * iqr
    upper_fence = q3 + BATCH_IQR_FACTOR * iqr

    outliers = []

    for row in valid_rows:
        rate = row["_metric_value"]

        if rate < lower_fence or rate > upper_fence:
            outliers.append({
                "entity": row["_label"],
                "defect_rate": rate,
                "production": _number(row.get("production")),
                "defects": _number(row.get("defects")),
                "position": (
                    "above_upper_fence"
                    if rate > upper_fence
                    else "below_lower_fence"
                ),
            })

    return {
        "method": "IQR",
        "iqr_factor": BATCH_IQR_FACTOR,
        "comparison_count": len(valid_rows),
        "median_defect_rate": _quantile(rates, 0.5),
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "lower_fence": lower_fence,
        "upper_fence": upper_fence,
        "detected": bool(outliers),
        "outliers": outliers,
        "reason": (
            "Có batch nằm ngoài ngưỡng IQR."
            if outliers
            else "Không có batch nào nằm ngoài ngưỡng IQR."
        ),
    }


# ============================================================
# MODEL QUALITY DIFFERENCE
# ============================================================

def _analyze_model_rate_spread(kpis: dict) -> dict:
    config = GROUP_CONFIG["model"]
    rows = _to_rows(kpis.get(config["field"]))

    valid_rows = _valid_metric_rows(
        rows,
        config["label_field"],
        "defect_rate",
    )

    if len(valid_rows) < 2:
        return {
            "detected": False,
            "method": "max_min_defect_rate_spread",
            "reason": "Cần ít nhất hai model có tỷ lệ lỗi hợp lệ.",
        }

    lowest = min(valid_rows, key=lambda row: row["_metric_value"])
    highest = max(valid_rows, key=lambda row: row["_metric_value"])

    spread = highest["_metric_value"] - lowest["_metric_value"]

    return {
        "method": "max_min_defect_rate_spread",
        "metric": "defect_rate",
        "unit": "percentage_points",
        "lowest_model": lowest["_label"],
        "lowest_defect_rate": lowest["_metric_value"],
        "highest_model": highest["_label"],
        "highest_defect_rate": highest["_metric_value"],
        "spread_percentage_points": spread,
        "configured_threshold_percentage_points": (
            MODEL_RATE_SPREAD_THRESHOLD_PP
        ),
        "detected": spread >= MODEL_RATE_SPREAD_THRESHOLD_PP,
        "reason": (
            "Độ chênh lệch đạt hoặc vượt ngưỡng cấu hình."
            if spread >= MODEL_RATE_SPREAD_THRESHOLD_PP
            else "Độ chênh lệch chưa đạt ngưỡng cấu hình."
        ),
        "interpretation_limit": (
            "Tỷ lệ lỗi là chỉ số dùng để so sánh chất lượng đầu ra; "
            "không tự chứng minh nguyên nhân gây lỗi."
        ),
    }


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def detect_hardware_anomalies(report: Any) -> dict:
    """
    Phát hiện bất thường từ KPI do backend đã tính.

    Không sửa dữ liệu đầu vào.
    Không gọi AI.
    Không suy luận nguyên nhân gốc rễ.
    """
    kpis = _to_dict(report)

    if not kpis:
        return {
            "status": "insufficient_data",
            "findings": {},
        }

    group_extremes = _analyze_group_extremes(kpis)

    findings = {
        **group_extremes,
        "largest_daily_change": _analyze_daily_change(kpis),
        "unusual_batch_defect_rate": _analyze_batch_outliers(kpis),
        "model_quality_difference": _analyze_model_rate_spread(kpis),
    }

    return {
        "status": "completed",
        "rules_version": "1.0",
        "findings": findings,
    }
