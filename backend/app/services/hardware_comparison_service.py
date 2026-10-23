from __future__ import annotations

from datetime import date
from typing import Any, Literal

import pandas as pd

from app.services.data_quality_service import _to_numeric
from app.services.hardware_service import (
    calculate_hardware_kpis,
    clean_dataframe,
    find_column,
)


ComparisonDimension = Literal["Supplier", "Model", "Component", "Batch"]
DIMENSION_FIELDS: dict[ComparisonDimension, str] = {
    "Supplier": "supplier",
    "Model": "model",
    "Component": "component",
    "Batch": "batch",
}
METRICS = ("production", "defects", "defect_rate", "downtime")


def _parse_dates(
    dataframe: pd.DataFrame,
    date_column: str,
) -> pd.Series:
    values = dataframe[date_column]
    parsed = pd.to_datetime(values, format="ISO8601", errors="coerce")
    return parsed.dt.date


def _clean_period_rows(
    dataframe: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    production_column = find_column(dataframe, "production")
    if production_column is None:
        raise ValueError(
            "Không tìm thấy cột sản lượng trong dữ liệu."
        )

    defects_column = find_column(dataframe, "defects")
    downtime_column = find_column(dataframe, "downtime")
    production = _to_numeric(dataframe[production_column])
    valid_mask = production.notna() & production.ge(0)

    if defects_column is not None:
        defects = _to_numeric(dataframe[defects_column])
        valid_mask &= defects.notna() & defects.ge(0)
    if downtime_column is not None:
        downtime = _to_numeric(dataframe[downtime_column])
        valid_mask &= downtime.notna() & downtime.ge(0)

    valid_rows = dataframe.loc[valid_mask].copy()
    excluded_rows = int(len(dataframe) - len(valid_rows))
    return valid_rows, excluded_rows


def _calculate_metrics(
    dataframe: pd.DataFrame,
) -> tuple[dict[str, Any] | None, int]:
    valid_rows, excluded_rows = _clean_period_rows(dataframe)
    if valid_rows.empty:
        return None, excluded_rows

    kpis = calculate_hardware_kpis(valid_rows)
    production = kpis["total_production"]
    metrics = {
        "production": kpis["total_production"],
        "defects": kpis["total_defects"],
        "defect_rate": (
            kpis["defect_rate"] if production > 0 else None
        ),
        "downtime": kpis["total_downtime"],
        "rows_used_for_kpi": kpis["data_processing"]["rows_used_for_kpi"],
        "rows_excluded_from_kpi": excluded_rows,
    }
    return metrics, excluded_rows


def _percent_change(
    current: float | None,
    baseline: float | None,
) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return round((current - baseline) / abs(baseline) * 100, 2)


def _metric_differences(
    period_a: dict[str, Any] | None,
    period_b: dict[str, Any] | None,
) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    for metric in METRICS:
        value_a = period_a.get(metric) if period_a else None
        value_b = period_b.get(metric) if period_b else None
        delta = (
            round(value_b - value_a, 2)
            if value_a is not None and value_b is not None
            else None
        )
        result[metric] = {
            "absolute": delta,
            "percentage_change": _percent_change(value_b, value_a),
        }
        if metric == "defect_rate":
            result[metric]["percentage_points"] = delta
    return result


def _period_summary(
    rows: pd.DataFrame,
    start_date: date,
    end_date: date,
    *,
    invalid_date_rows_exist: bool,
    deduplicated_rows_exist: bool,
    missing_optional_metrics: list[str],
    missing_dimension_rows: int = 0,
) -> dict[str, Any]:
    metrics, excluded_rows = _calculate_metrics(rows)
    if metrics is None:
        status = "not_comparable"
    elif (
        excluded_rows > 0
        or invalid_date_rows_exist
        or deduplicated_rows_exist
        or missing_optional_metrics
        or missing_dimension_rows > 0
    ):
        status = "partial"
    else:
        status = "complete"

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows_in_period": len(rows),
        "rows_used_for_kpi": (
            metrics["rows_used_for_kpi"] if metrics else 0
        ),
        "rows_excluded_from_kpi": excluded_rows,
        "missing_dimension_rows": missing_dimension_rows,
        "status": status,
        "metrics": metrics,
    }


def _entity_name(value: Any) -> str:
    if pd.isna(value) or not str(value).strip():
        return "Không xác định"
    return str(value).strip()


def compare_hardware_periods(
    dataframe: pd.DataFrame,
    *,
    period_a_start: date,
    period_a_end: date,
    period_b_start: date,
    period_b_end: date,
    dimension: ComparisonDimension | None = None,
) -> dict[str, Any]:
    """Compare Python-calculated hardware KPIs across two inclusive date ranges."""
    if dataframe.empty:
        raise ValueError("Dataset không có dữ liệu để so sánh.")
    if period_a_start > period_a_end:
        raise ValueError("period_a_start phải nhỏ hơn hoặc bằng period_a_end.")
    if period_b_start > period_b_end:
        raise ValueError("period_b_start phải nhỏ hơn hoặc bằng period_b_end.")
    if not (
        period_a_end < period_b_start
        or period_b_end < period_a_start
    ):
        raise ValueError("Hai khoảng thời gian không được chồng lấn.")

    cleaned_dataframe = clean_dataframe(dataframe)
    date_column = find_column(cleaned_dataframe, "date")
    if date_column is None:
        raise ValueError(
            "Không tìm thấy cột ngày để so sánh khoảng thời gian."
        )
    dimension_column = None
    if dimension is not None:
        dimension_column = find_column(
            cleaned_dataframe,
            DIMENSION_FIELDS[dimension],
        )
        if dimension_column is None:
            raise ValueError(
                f"Không tìm thấy cột {dimension} trong dữ liệu."
            )
    if find_column(cleaned_dataframe, "production") is None:
        raise ValueError("Không tìm thấy cột sản lượng trong dữ liệu.")

    parsed_dates = _parse_dates(cleaned_dataframe, date_column)
    date_valid_mask = parsed_dates.notna()
    invalid_date_rows = int((~date_valid_mask).sum())
    period_a_mask = (
        date_valid_mask
        & parsed_dates.ge(period_a_start)
        & parsed_dates.le(period_a_end)
    )
    period_b_mask = (
        date_valid_mask
        & parsed_dates.ge(period_b_start)
        & parsed_dates.le(period_b_end)
    )

    optional_metrics = []
    if find_column(cleaned_dataframe, "defects") is None:
        optional_metrics.append("defects")
    if find_column(cleaned_dataframe, "downtime") is None:
        optional_metrics.append("downtime")

    period_rows = {
        "period_a": cleaned_dataframe.loc[period_a_mask].copy(),
        "period_b": cleaned_dataframe.loc[period_b_mask].copy(),
    }
    period_ranges = {
        "period_a": (period_a_start, period_a_end),
        "period_b": (period_b_start, period_b_end),
    }
    entity_groups: dict[str, dict[str, pd.DataFrame]] = {}
    missing_dimension_rows: dict[str, int] = {
        "period_a": 0,
        "period_b": 0,
    }

    if dimension_column is None:
        entity_groups["Tổng thể"] = period_rows.copy()
    else:
        for period_key, rows in period_rows.items():
            names = rows[dimension_column].map(_entity_name)
            missing_dimension_rows[period_key] = int(
                names.eq("Không xác định").sum()
            )
            for name in names.unique():
                group_mask = names.eq(name)
                entity_groups.setdefault(
                    name,
                    {"period_a": rows.iloc[0:0], "period_b": rows.iloc[0:0]},
                )[period_key] = rows.loc[group_mask].copy()

    comparisons = []
    period_summaries: dict[str, dict[str, Any]] = {}
    for period_key, rows in period_rows.items():
        start_date, end_date = period_ranges[period_key]
        period_summaries[period_key] = _period_summary(
            rows,
            start_date,
            end_date,
            invalid_date_rows_exist=invalid_date_rows > 0,
            deduplicated_rows_exist=(
                len(dataframe) > len(cleaned_dataframe)
            ),
            missing_optional_metrics=optional_metrics,
            missing_dimension_rows=missing_dimension_rows[period_key],
        )

    common_entity_count = 0
    for name in sorted(entity_groups, key=str.casefold):
        periods = entity_groups[name]
        period_a_rows = periods["period_a"]
        period_b_rows = periods["period_b"]
        period_a_metrics, period_a_excluded = _calculate_metrics(period_a_rows)
        period_b_metrics, period_b_excluded = _calculate_metrics(period_b_rows)
        entity_status = (
            "not_comparable"
            if period_a_metrics is None or period_b_metrics is None
            else "complete"
            if period_a_excluded == 0 and period_b_excluded == 0
            else "partial"
        )
        if period_a_metrics is not None and period_b_metrics is not None:
            common_entity_count += 1
        comparisons.append(
            {
                "entity": (
                    name if dimension_column is not None else None
                ),
                "period_a": period_a_metrics,
                "period_b": period_b_metrics,
                "differences": _metric_differences(
                    period_a_metrics,
                    period_b_metrics,
                ),
                "status": entity_status,
            }
        )

    non_comparable_period = any(
        summary["status"] == "not_comparable"
        for summary in period_summaries.values()
    )
    if non_comparable_period or (
        dimension_column is not None and common_entity_count == 0
    ):
        comparison_status = "not_comparable"
    elif (
        any(
            summary["status"] == "partial"
            for summary in period_summaries.values()
        )
        or any(item["status"] != "complete" for item in comparisons)
    ):
        comparison_status = "partial"
    else:
        comparison_status = "complete"

    warnings = []
    if invalid_date_rows:
        warnings.append(
            f"{invalid_date_rows} dòng có ngày thiếu hoặc không hợp lệ "
            "không thể xếp vào hai khoảng thời gian."
        )
    if any(
        summary["rows_excluded_from_kpi"] > 0
        for summary in period_summaries.values()
    ):
        warnings.append(
            "Một số dòng có Production, Defects hoặc Downtime thiếu, sai "
            "định dạng hoặc âm đã bị loại khỏi KPI theo quy tắc hiện có."
        )
    if optional_metrics:
        warnings.append(
            "Thiếu cột "
            + ", ".join(optional_metrics)
            + "; KPI đó sử dụng giá trị mặc định 0 như quy tắc hiện tại."
        )
    if dimension_column is not None and any(missing_dimension_rows.values()):
        warnings.append(
            "Một số dòng không có giá trị cho chiều phân tích và được gom "
            "vào nhóm 'Không xác định'."
        )
    if comparison_status == "not_comparable":
        warnings.append(
            "Không đủ dữ liệu hợp lệ ở cả hai khoảng hoặc không có nhóm "
            "chung để thực hiện so sánh."
        )

    return {
        "comparison_status": comparison_status,
        "dimension": dimension,
        "periods": period_summaries,
        "comparisons": comparisons,
        "data_quality": {
            "rows_after_cleaning": len(cleaned_dataframe),
            "rows_removed_by_cleaning": max(
                0,
                len(dataframe) - len(cleaned_dataframe),
            ),
            "invalid_date_rows": invalid_date_rows,
            "optional_metrics_defaulted_to_zero": optional_metrics,
        },
        "warnings": warnings,
        "calculation": {
            "rows_with_negative_or_invalid_metrics_excluded": True,
            "defect_rate_formula": (
                "total_defects / total_production * 100"
            ),
            "defect_rate_difference_unit": "percentage_points",
            "percentage_change_baseline": "period_a",
            "percentage_change_is_null_when_baseline_is_zero": True,
        },
    }
