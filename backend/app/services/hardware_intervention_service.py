from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.services.multi_file_service import _parse_numeric


def _parse_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = pd.to_datetime(
            value.strip(),
            format="ISO8601",
            errors="raise",
        )
    except (TypeError, ValueError):
        for date_format in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = pd.to_datetime(
                    value.strip(),
                    format=date_format,
                    errors="raise",
                )
                break
            except (TypeError, ValueError):
                continue
        else:
            return None
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date()


def _parse_metric(value: Any) -> tuple[float | None, str | None]:
    if value is None or pd.isna(value):
        return None, "missing"
    parsed, issue = _parse_numeric(value)
    if issue is not None or parsed is None:
        return None, issue or "missing"
    return float(parsed), None


def _percent_change(after: float | None, before: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return round((after - before) / abs(before) * 100, 2)


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _period_metrics(
    dataframe: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
    model: str | None,
) -> dict[str, Any]:
    days = (end_date - start_date).days + 1
    if "Date" not in dataframe.columns:
        return {
            "days": days,
            "rows_in_range": 0,
            "records_used": {},
            "records_excluded": {},
            "totals": {
                "production": None,
                "defects": None,
                "defect_rate": None,
                "downtime": None,
            },
            "production_per_day": None,
            "downtime_per_day": None,
            "data_issues": {"missing_date_column": True},
        }

    selected = dataframe
    if model is not None:
        if "Model" not in selected.columns:
            raise ValueError(
                "Không thể lọc model vì dataset không có cột Model."
            )
        model_values = (
            selected["Model"]
            .astype("string")
            .str.strip()
            .str.casefold()
        )
        selected = selected.loc[
            model_values == model.strip().casefold()
        ]

    dates = selected["Date"].map(_parse_date)
    invalid_date_rows = int(dates.isna().sum())
    selected = selected.loc[
        dates.map(
            lambda value: value is not None
            and start_date <= value <= end_date
        )
    ].copy()

    parsed_values: dict[str, list[tuple[float | None, str | None]]] = {}
    for column in ("Production", "Defects", "Downtime"):
        if column in selected.columns:
            parsed_values[column] = [
                _parse_metric(value) for value in selected[column]
            ]
        else:
            parsed_values[column] = [
                (None, "missing_column") for _ in range(len(selected))
            ]

    valid: dict[str, list[float]] = {
        column: [
            value
            for value, issue in values
            if value is not None and issue is None
        ]
        for column, values in parsed_values.items()
    }
    production_and_defects = [
        (production, defects)
        for (production, production_issue), (defects, defects_issue)
        in zip(parsed_values["Production"], parsed_values["Defects"])
        if production_issue is None
        and defects_issue is None
        and production is not None
        and defects is not None
    ]
    paired_production = sum(row[0] for row in production_and_defects)
    paired_defects = sum(row[1] for row in production_and_defects)
    production_total = (
        sum(valid["Production"])
        if "Production" in selected.columns and valid["Production"]
        else None
    )
    defects_total = (
        sum(valid["Defects"])
        if "Defects" in selected.columns and valid["Defects"]
        else None
    )
    downtime_total = (
        sum(valid["Downtime"])
        if "Downtime" in selected.columns and valid["Downtime"]
        else None
    )
    issue_counts = {
        column: {
            issue or "valid": sum(
                1 for _, row_issue in values if row_issue == issue
            )
            for issue in ("missing", "invalid", "negative", "missing_column")
            if any(row_issue == issue for _, row_issue in values)
        }
        for column, values in parsed_values.items()
    }
    return {
        "days": days,
        "rows_in_range": len(selected),
        "records_used": {
            column.casefold(): len(values)
            for column, values in valid.items()
        }
        | {"defect_rate": len(production_and_defects)},
        "records_excluded": {
            column.casefold(): len(selected) - len(values)
            for column, values in valid.items()
        }
        | {
            "defect_rate": (
                len(selected) - len(production_and_defects)
            )
        },
        "totals": {
            "production": _rounded(production_total),
            "defects": _rounded(defects_total),
            "defect_rate": (
                _rounded(paired_defects / paired_production * 100)
                if paired_production > 0
                else None
            ),
            "downtime": _rounded(downtime_total),
        },
        "production_per_day": (
            _rounded(production_total / days)
            if production_total is not None
            else None
        ),
        "downtime_per_day": (
            _rounded(downtime_total / days)
            if downtime_total is not None
            else None
        ),
        "data_issues": {
            "invalid_date_rows_in_dataset": invalid_date_rows,
            "metric_rows": issue_counts,
        },
    }


def _comparison(
    before: float | None,
    after: float | None,
    *,
    unit: str,
) -> dict[str, Any]:
    return {
        "before": before,
        "after": after,
        "change_absolute": (
            _rounded(after - before)
            if before is not None and after is not None
            else None
        ),
        "change_percent": _percent_change(after, before),
        "unit": unit,
    }


def evaluate_hardware_intervention(
    dataframe: pd.DataFrame,
    *,
    before_start_date: date,
    before_end_date: date,
    after_start_date: date,
    after_end_date: date,
    model: str | None = None,
) -> dict[str, Any]:
    """Compare production KPIs in two non-overlapping date ranges."""
    if before_start_date > before_end_date:
        raise ValueError("before_start_date phải trước hoặc bằng before_end_date.")
    if after_start_date > after_end_date:
        raise ValueError("after_start_date phải trước hoặc bằng after_end_date.")
    if before_end_date >= after_start_date:
        raise ValueError(
            "Khoảng trước can thiệp phải kết thúc trước khoảng sau can thiệp."
        )
    if model is not None and not model.strip():
        raise ValueError("model không được để trống.")

    before = _period_metrics(
        dataframe,
        start_date=before_start_date,
        end_date=before_end_date,
        model=model,
    )
    after = _period_metrics(
        dataframe,
        start_date=after_start_date,
        end_date=after_end_date,
        model=model,
    )

    before_totals = before["totals"]
    after_totals = after["totals"]
    defect_rate = _comparison(
        before_totals["defect_rate"],
        after_totals["defect_rate"],
        unit="%",
    )
    defect_rate["change_absolute_percentage_points"] = (
        defect_rate.pop("change_absolute")
    )
    production = _comparison(
        before_totals["production"],
        after_totals["production"],
        unit="sản phẩm",
    )
    production["per_day"] = _comparison(
        before["production_per_day"],
        after["production_per_day"],
        unit="sản phẩm/ngày",
    )
    defects = _comparison(
        before_totals["defects"],
        after_totals["defects"],
        unit="lỗi",
    )
    downtime = _comparison(
        before_totals["downtime"],
        after_totals["downtime"],
        unit="phút",
    )
    downtime["per_day"] = _comparison(
        before["downtime_per_day"],
        after["downtime_per_day"],
        unit="phút/ngày",
    )

    data_warnings: list[str] = []
    operational_warnings: list[str] = []

    if "Date" not in dataframe.columns:
        data_warnings.append(
            "Dataset thiếu cột Date nên không thể xác định kỳ."
        )

    if before["rows_in_range"] == 0:
        data_warnings.append(
            "Không có bản ghi phù hợp trong khoảng trước can thiệp."
        )

    if after["rows_in_range"] == 0:
        data_warnings.append(
            "Không có bản ghi phù hợp trong khoảng sau can thiệp."
        )

    if defect_rate["before"] is None or defect_rate["after"] is None:
        data_warnings.append(
            "Thiếu dữ liệu Production/Defects hợp lệ để tính tỷ lệ lỗi "
            "ở cả hai kỳ."
        )

    if downtime["before"] is None or downtime["after"] is None:
        data_warnings.append(
            "Thiếu dữ liệu Downtime hợp lệ ở một hoặc cả hai kỳ."
        )

    invalid_date_rows = any(
        period["data_issues"].get("invalid_date_rows_in_dataset", 0)
        for period in (before, after)
    )
    if invalid_date_rows:
        data_warnings.append(
            "Một số dòng có ngày thiếu hoặc không hợp lệ đã bị loại khỏi "
            "so sánh."
        )

    for phase_name, period in (("trước", before), ("sau", after)):
        if any(
            count
            for column_issues in period["data_issues"].get(
                "metric_rows",
                {}
            ).values()
            for count in column_issues.values()
        ):
            data_warnings.append(
                f"Một số giá trị KPI trong kỳ {phase_name} bị thiếu "
                "hoặc không hợp lệ."
            )

    excluded_metric_rows = any(
        count
        for period in (before, after)
        for count in period["records_excluded"].values()
    )

    required_values = (
        defect_rate["before"],
        defect_rate["after"],
        production["per_day"]["before"],
        production["per_day"]["after"],
        downtime["per_day"]["before"],
        downtime["per_day"]["after"],
    )

    operational_tradeoff = False
    before_production_per_day = production["per_day"]["before"]
    after_production_per_day = production["per_day"]["after"]
    before_downtime_per_day = downtime["per_day"]["before"]
    after_downtime_per_day = downtime["per_day"]["after"]

    if (
        before_production_per_day is not None
        and after_production_per_day is not None
        and after_production_per_day < before_production_per_day
    ):
        operational_tradeoff = True
        change = _percent_change(
            after_production_per_day,
            before_production_per_day,
        )
        operational_warnings.append(
            f"Sản lượng/ngày giảm {abs(change):.2f}% sau can thiệp."
            if change is not None
            else "Sản lượng/ngày giảm sau can thiệp."
        )

    if (
        before_downtime_per_day is not None
        and after_downtime_per_day is not None
        and after_downtime_per_day > before_downtime_per_day
    ):
        operational_tradeoff = True
        change = _percent_change(
            after_downtime_per_day,
            before_downtime_per_day,
        )
        operational_warnings.append(
            f"Downtime/ngày tăng {change:.2f}% sau can thiệp."
            if change is not None
            else "Downtime/ngày tăng sau can thiệp."
        )

    all_warnings = data_warnings + operational_warnings

    if any(value is None for value in required_values):
        conclusion = "insufficient_data"
        conclusion_reason = "missing_comparable_kpi"
    elif invalid_date_rows or excluded_metric_rows:
        conclusion = "insufficient_data"
        conclusion_reason = "incomplete_data_quality"
    elif before["records_used"].get("defect_rate", 0) == 0 or after[
        "records_used"
    ].get("defect_rate", 0) == 0:
        conclusion = "insufficient_data"
        conclusion_reason = "no_valid_paired_records"
    elif math.isclose(
        defect_rate["before"],
        defect_rate["after"],
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        conclusion = "not_improved"
        conclusion_reason = "defect_rate_unchanged"
    elif defect_rate["after"] > defect_rate["before"]:
        conclusion = "not_improved"
        conclusion_reason = "defect_rate_increased"
    elif operational_tradeoff:
        conclusion = "mixed_result"
        conclusion_reason = "quality_improved_with_operational_tradeoff"
    else:
        conclusion = "improved"
        conclusion_reason = "defect_rate_decreased_without_operational_decline"

    summary_by_conclusion = {
        "improved": (
            "Có dấu hiệu cải thiện chất lượng: tỷ lệ lỗi giảm và không ghi nhận "
            "sản lượng/ngày giảm hoặc downtime/ngày tăng."
        ),
        "not_improved": (
            "Chưa ghi nhận cải thiện chất lượng theo tỷ lệ lỗi."
        ),
        "mixed_result": (
            "Tỷ lệ lỗi giảm nhưng có đánh đổi về vận hành. "
            "Cần đánh giá thêm trước khi kết luận can thiệp thành công."
        ),
        "insufficient_data": (
            "Chưa đủ dữ liệu hợp lệ để kết luận hiệu quả tổng thể."
        ),
    }
    return {
        "title": "Đánh giá hiệu quả can thiệp sản xuất",
        "summary": summary_by_conclusion[conclusion],
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "model": model,
        "periods": {
            "before": {
                "start_date": before_start_date.isoformat(),
                "end_date": before_end_date.isoformat(),
                **before,
            },
            "after": {
                "start_date": after_start_date.isoformat(),
                "end_date": after_end_date.isoformat(),
                **after,
            },
        },
        "metrics": {
            "production": production,
            "defects": defects,
            "defect_rate": defect_rate,
            "downtime": downtime,
        },
        "data_quality": {
            "status": "partial" if data_warnings else "complete",
            "warnings": data_warnings,
        },
        "warnings": all_warnings,
        "operational_warnings": operational_warnings,
        "ai_explanation": None,
        "ai_status": "not_requested",
    }
