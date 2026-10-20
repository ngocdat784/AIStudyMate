from __future__ import annotations

import calendar
import math
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd

from app.services.multi_file_service import _parse_numeric


Granularity = Literal["day", "week", "month"]
PRODUCTION_DROP_THRESHOLD_PERCENT = 20.0
DEFECT_INCREASE_THRESHOLD_PERCENT = 20.0
DOWNTIME_INCREASE_THRESHOLD_PERCENT = 20.0
DEFECT_RATE_INCREASE_THRESHOLD_POINTS = 0.5


def _parse_record_date(value: Any) -> date | None:
    if _is_missing_value(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
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
        return None
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date()


def _parse_metric(value: Any) -> tuple[float | None, str | None]:
    if _is_missing_value(value):
        return None, "missing"
    parsed, issue = _parse_numeric(value)
    if issue is not None or parsed is None:
        return None, issue or "missing"
    return float(parsed), None


def _is_missing_value(value: Any) -> bool:
    try:
        missing = pd.isna(value)
        return bool(missing)
    except (TypeError, ValueError):
        return value is None


def _period_start(value: date, granularity: Granularity) -> date:
    if granularity == "day":
        return value
    if granularity == "week":
        return value - pd.Timedelta(days=value.weekday())
    return date(value.year, value.month, 1)


def _period_end(start: date, granularity: Granularity) -> date:
    if granularity == "day":
        return start
    if granularity == "week":
        return start + pd.Timedelta(days=6)
    return date(
        start.year,
        start.month,
        calendar.monthrange(start.year, start.month)[1],
    )


def _period_ordinal(value: date, granularity: Granularity) -> int:
    if granularity == "day":
        return value.toordinal()
    if granularity == "week":
        return value.toordinal() // 7
    return value.year * 12 + value.month - 1


def _percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _direction(first: float | None, last: float | None) -> str:
    if first is None or last is None:
        return "insufficient_data"
    if math.isclose(first, last, rel_tol=0.0, abs_tol=1e-12):
        return "unchanged"
    return "increasing" if last > first else "decreasing"


def _aggregate_period(
    period: date,
    rows: list[dict[str, Any]],
    granularity: Granularity,
) -> dict[str, Any]:
    production_rows = [
        row for row in rows if row["production_issue"] is None
    ]
    defect_rows = [row for row in rows if row["defects_issue"] is None]
    downtime_rows = [row for row in rows if row["downtime_issue"] is None]
    paired_rows = [
        row
        for row in rows
        if row["production_issue"] is None
        and row["defects_issue"] is None
    ]

    production = sum(row["production"] for row in production_rows)
    defects = sum(row["defects"] for row in defect_rows)
    downtime = sum(row["downtime"] for row in downtime_rows)
    paired_production = sum(row["production"] for row in paired_rows)
    paired_defects = sum(row["defects"] for row in paired_rows)

    return {
        "period_start": period.isoformat(),
        "period_end": _period_end(period, granularity).isoformat(),
        "production": round(production, 2),
        "defects": round(defects, 2),
        "defect_rate": (
            round(paired_defects / paired_production * 100, 2)
            if paired_production > 0
            else None
        ),
        "downtime": round(downtime, 2),
        "rows_in_period": len(rows),
        "rows_used": len(production_rows),
        "rows_excluded": len(rows) - len(production_rows),
        "records_used": {
            "production": len(production_rows),
            "defects": len(defect_rows),
            "defect_rate": len(paired_rows),
            "downtime": len(downtime_rows),
        },
        "records_excluded": {
            "production": len(rows) - len(production_rows),
            "defects": len(rows) - len(defect_rows),
            "defect_rate": len(rows) - len(paired_rows),
            "downtime": len(rows) - len(downtime_rows),
        },
    }


def _build_period_changes(
    time_series: list[dict[str, Any]],
    granularity: Granularity,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for previous, current in zip(time_series, time_series[1:]):
        previous_start = date.fromisoformat(previous["period_start"])
        current_start = date.fromisoformat(current["period_start"])
        missing_periods = max(
            0,
            _period_ordinal(current_start, granularity)
            - _period_ordinal(previous_start, granularity)
            - 1,
        )
        production_delta = round(
            current["production"] - previous["production"],
            2,
        )
        defects_delta = round(
            current["defects"] - previous["defects"],
            2,
        )
        downtime_delta = round(
            current["downtime"] - previous["downtime"],
            2,
        )
        current_rate = current["defect_rate"]
        previous_rate = previous["defect_rate"]

        changes.append(
            {
                "previous_period_start": previous["period_start"],
                "period_start": current["period_start"],
                "missing_periods_between": missing_periods,
                "production_delta": production_delta,
                "production_change_percent": _percent_change(
                    current["production"],
                    previous["production"],
                ),
                "defects_delta": defects_delta,
                "defects_change_percent": _percent_change(
                    current["defects"],
                    previous["defects"],
                ),
                "defect_rate_delta_percentage_points": (
                    round(current_rate - previous_rate, 2)
                    if current_rate is not None and previous_rate is not None
                    else None
                ),
                "downtime_delta": downtime_delta,
                "downtime_change_percent": _percent_change(
                    current["downtime"],
                    previous["downtime"],
                ),
            }
        )
    return changes


def _build_anomalies(
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for change in changes:
        if (
            change["production_change_percent"] is not None
            and change["production_change_percent"]
            <= -PRODUCTION_DROP_THRESHOLD_PERCENT
        ):
            anomalies.append(
                {
                    "type": "production_drop",
                    "severity": "notable",
                    "previous_period_start": change["previous_period_start"],
                    "period_start": change["period_start"],
                    "observed_change_percent": (
                        change["production_change_percent"]
                    ),
                    "threshold_percent": (
                        -PRODUCTION_DROP_THRESHOLD_PERCENT
                    ),
                    "message": "Sản lượng giảm đáng kể so với kỳ trước.",
                }
            )

        if (
            change["defects_change_percent"] is not None
            and change["defects_change_percent"]
            >= DEFECT_INCREASE_THRESHOLD_PERCENT
        ):
            anomalies.append(
                {
                    "type": "defects_increase",
                    "severity": "notable",
                    "previous_period_start": change["previous_period_start"],
                    "period_start": change["period_start"],
                    "observed_change_percent": (
                        change["defects_change_percent"]
                    ),
                    "threshold_percent": DEFECT_INCREASE_THRESHOLD_PERCENT,
                    "message": "Số lỗi tăng đáng kể so với kỳ trước.",
                }
            )

        rate_delta = change["defect_rate_delta_percentage_points"]
        if (
            rate_delta is not None
            and rate_delta >= DEFECT_RATE_INCREASE_THRESHOLD_POINTS
        ):
            anomalies.append(
                {
                    "type": "defect_rate_increase",
                    "severity": "notable",
                    "previous_period_start": change["previous_period_start"],
                    "period_start": change["period_start"],
                    "observed_change_percentage_points": rate_delta,
                    "threshold_percentage_points": (
                        DEFECT_RATE_INCREASE_THRESHOLD_POINTS
                    ),
                    "message": (
                        "Tỷ lệ lỗi tăng đáng kể so với kỳ trước."
                    ),
                }
            )

        if (
            change["downtime_change_percent"] is not None
            and change["downtime_change_percent"]
            >= DOWNTIME_INCREASE_THRESHOLD_PERCENT
        ):
            anomalies.append(
                {
                    "type": "downtime_increase",
                    "severity": "notable",
                    "previous_period_start": change["previous_period_start"],
                    "period_start": change["period_start"],
                    "observed_change_percent": (
                        change["downtime_change_percent"]
                    ),
                    "threshold_percent": (
                        DOWNTIME_INCREASE_THRESHOLD_PERCENT
                    ),
                    "message": (
                        "Downtime tăng đáng kể so với kỳ trước."
                    ),
                }
            )
    return anomalies


def analyze_hardware_trends(
    dataframe: pd.DataFrame,
    granularity: Granularity,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    if dataframe.empty:
        raise ValueError("Dataset không có dữ liệu để phân tích xu hướng.")
    if "Date" not in dataframe.columns or "Production" not in dataframe.columns:
        raise ValueError(
            "Dataset cần có các cột chuẩn Date và Production."
        )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date phải nhỏ hơn hoặc bằng end_date.")

    grouped_rows: dict[date, list[dict[str, Any]]] = {}
    invalid_date_rows = 0
    missing_date_rows = 0
    rows_outside_range = 0
    invalid_numeric_by_metric = {
        "production": 0,
        "defects": 0,
        "downtime": 0,
    }
    missing_numeric_by_metric = {
        "production": 0,
        "defects": 0,
        "downtime": 0,
    }

    for record in dataframe.to_dict(orient="records"):
        raw_date = record.get("Date")
        parsed_date = _parse_record_date(raw_date)
        if parsed_date is None:
            if _is_missing_value(raw_date) or (
                isinstance(raw_date, str) and not raw_date.strip()
            ):
                missing_date_rows += 1
            else:
                invalid_date_rows += 1
            continue

        if (
            start_date is not None
            and parsed_date < start_date
            or end_date is not None
            and parsed_date > end_date
        ):
            rows_outside_range += 1
            continue

        production, production_issue = _parse_metric(
            record.get("Production")
        )
        defects, defects_issue = _parse_metric(record.get("Defects"))
        downtime, downtime_issue = _parse_metric(record.get("Downtime"))

        for metric, issue in (
            ("production", production_issue),
            ("defects", defects_issue),
            ("downtime", downtime_issue),
        ):
            if issue == "invalid" or issue == "negative":
                invalid_numeric_by_metric[metric] += 1
            elif issue == "missing":
                missing_numeric_by_metric[metric] += 1

        period = _period_start(parsed_date, granularity)
        grouped_rows.setdefault(period, []).append(
            {
                "production": production,
                "production_issue": production_issue,
                "defects": defects,
                "defects_issue": defects_issue,
                "downtime": downtime,
                "downtime_issue": downtime_issue,
            }
        )

    time_series = [
        _aggregate_period(period, grouped_rows[period], granularity)
        for period in sorted(grouped_rows)
    ]
    period_changes = _build_period_changes(time_series, granularity)
    anomalies = _build_anomalies(period_changes)

    valid_production = [
        row for rows in grouped_rows.values() for row in rows
        if row["production_issue"] is None
    ]
    valid_defects = [
        row for rows in grouped_rows.values() for row in rows
        if row["defects_issue"] is None
    ]
    valid_downtime = [
        row for rows in grouped_rows.values() for row in rows
        if row["downtime_issue"] is None
    ]
    valid_pairs = [
        row for rows in grouped_rows.values() for row in rows
        if row["production_issue"] is None
        and row["defects_issue"] is None
    ]

    total_production = sum(row["production"] for row in valid_production)
    total_defects = sum(row["defects"] for row in valid_defects)
    total_downtime = sum(row["downtime"] for row in valid_downtime)
    rate_production = sum(row["production"] for row in valid_pairs)
    rate_defects = sum(row["defects"] for row in valid_pairs)
    first_period = time_series[0] if time_series else None
    last_period = time_series[-1] if time_series else None

    trends: dict[str, Any] = {}
    for metric in ("production", "defects", "defect_rate", "downtime"):
        first_value = first_period.get(metric) if first_period else None
        last_value = last_period.get(metric) if last_period else None
        delta = (
            round(last_value - first_value, 2)
            if first_value is not None and last_value is not None
            else None
        )
        trends[metric] = {
            "direction": _direction(first_value, last_value),
            "first_period_value": first_value,
            "last_period_value": last_value,
            "absolute_change": delta,
            "change_percent": (
                _percent_change(last_value, first_value)
                if first_value is not None and last_value is not None
                and metric != "defect_rate"
                else None
            ),
            "change_percentage_points": (
                delta if metric == "defect_rate" else None
            ),
        }

    rows_in_range = sum(
        period["rows_in_period"] for period in time_series
    )
    rows_used = sum(period["rows_used"] for period in time_series)

    warnings = []
    if invalid_date_rows or missing_date_rows:
        warnings.append(
            "Bản ghi thiếu hoặc sai định dạng Date đã bị loại khỏi chuỗi thời gian."
        )
    if any(invalid_numeric_by_metric.values()) or any(
        missing_numeric_by_metric.values()
    ):
        warnings.append(
            "Giá trị metric thiếu, sai định dạng hoặc âm không được dùng "
            "cho metric tương ứng."
        )
    if any(
        change["missing_periods_between"] > 0 for change in period_changes
    ):
        warnings.append(
            "Chuỗi thời gian chỉ chứa kỳ có dữ liệu; không tự điền kỳ trống."
        )

    return {
        "status": "completed_with_warnings" if warnings else "completed",
        "granularity": granularity,
        "week_definition": (
            "ISO 8601, bắt đầu thứ Hai"
            if granularity == "week"
            else None
        ),
        "requested_range": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "summary": {
            "periods_count": len(time_series),
            "first_period_start": (
                time_series[0]["period_start"] if time_series else None
            ),
            "last_period_start": (
                time_series[-1]["period_start"] if time_series else None
            ),
            "rows_in_range": rows_in_range,
            "rows_used": rows_used,
            "rows_excluded": rows_in_range - rows_used,
            "rows_outside_requested_range": rows_outside_range,
            "invalid_date_rows": invalid_date_rows,
            "missing_date_rows": missing_date_rows,
            "records_used": {
                "production": len(valid_production),
                "defects": len(valid_defects),
                "defect_rate": len(valid_pairs),
                "downtime": len(valid_downtime),
            },
            "records_excluded": {
                "production": (
                    rows_in_range - len(valid_production)
                ),
                "defects": rows_in_range - len(valid_defects),
                "defect_rate": rows_in_range - len(valid_pairs),
                "downtime": rows_in_range - len(valid_downtime),
            },
            "invalid_numeric_values": invalid_numeric_by_metric,
            "missing_numeric_values": missing_numeric_by_metric,
            "totals": {
                "production": round(total_production, 2),
                "defects": round(total_defects, 2),
                "defect_rate": (
                    round(rate_defects / rate_production * 100, 2)
                    if rate_production > 0
                    else None
                ),
                "downtime": round(total_downtime, 2),
            },
            "overall_trends": trends,
        },
        "time_series": time_series,
        "period_changes": period_changes,
        "anomalies": anomalies,
        "anomaly_thresholds": {
            "production_drop_percent": PRODUCTION_DROP_THRESHOLD_PERCENT,
            "defects_increase_percent": DEFECT_INCREASE_THRESHOLD_PERCENT,
            "defect_rate_increase_percentage_points": (
                DEFECT_RATE_INCREASE_THRESHOLD_POINTS
            ),
            "downtime_increase_percent": DOWNTIME_INCREASE_THRESHOLD_PERCENT,
            "comparison_basis": "kỳ có dữ liệu liền trước",
        },
        "warnings": warnings,
    }
