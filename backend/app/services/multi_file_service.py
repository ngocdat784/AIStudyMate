from __future__ import annotations

import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import pandas as pd


CANONICAL_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "Date": (
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
    ),
    "Product": (
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
    ),
    "Model": (
        "model",
        "model_name",
        "model_id",
        "model_code",
        "product_model",
        "mẫu",
        "mau",
        "mã model",
        "ma_model",
        "ten_model",
        "tên model",
    ),
    "Component": (
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
    ),
    "Supplier": (
        "supplier",
        "supplier_name",
        "supplier_id",
        "vendor",
        "vendor_name",
        "nhà cung cấp",
        "nha_cung_cap",
        "ma_nha_cung_cap",
        "mã nhà cung cấp",
        "ten_nha_cung_cap",
        "tên nhà cung cấp",
    ),
    "Batch": (
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
    ),
    "Production": (
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
    ),
    "Defects": (
        "defect",
        "defects",
        "defect_qty",
        "defect_quantity",
        "defective_quantity",
        "defect_count",
        "lỗi",
        "loi",
        "số lỗi",
        "so_loi",
        "loi_qty",
        "so_luong_loi",
        "số lượng lỗi",
    ),
    "Downtime": (
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
    ),
    "Defect Type": (
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
    ),
    "Quality Status": (
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
    ),
}

NUMERIC_COLUMNS = ("Production", "Defects", "Downtime")
REQUIRED_COLUMNS = ("Production",)
OPTIONAL_COLUMNS = tuple(
    column
    for column in CANONICAL_COLUMN_ALIASES
    if column not in REQUIRED_COLUMNS
)
MAX_ISSUE_SAMPLES = 20
_THOUSANDS_NUMBER = re.compile(
    r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$"
)
_ISO_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


@dataclass
class PreparedHardwareFile:
    filename: str
    dataframe: pd.DataFrame | None
    report: dict[str, Any]


@dataclass
class MultiFileNormalization:
    report: dict[str, Any]
    files: list[PreparedHardwareFile]


@dataclass
class MergedHardwareDataset:
    report: dict[str, Any]
    dataframe: pd.DataFrame


DATASET_CACHE_TTL_SECONDS = 60 * 60
MAX_CACHED_DATASETS = 25
_dataset_cache: OrderedDict[str, tuple[float, pd.DataFrame]] = OrderedDict()
_dataset_cache_lock = threading.RLock()


def store_merged_hardware_dataset(dataframe: pd.DataFrame) -> str:
    now = time.monotonic()
    with _dataset_cache_lock:
        expired_ids = [
            dataset_id
            for dataset_id, (created_at, _) in _dataset_cache.items()
            if now - created_at >= DATASET_CACHE_TTL_SECONDS
        ]
        for dataset_id in expired_ids:
            del _dataset_cache[dataset_id]

        dataset_id = uuid.uuid4().hex
        _dataset_cache[dataset_id] = (now, dataframe.copy(deep=True))
        while len(_dataset_cache) > MAX_CACHED_DATASETS:
            _dataset_cache.popitem(last=False)
        return dataset_id


def get_merged_hardware_dataset(dataset_id: str) -> pd.DataFrame | None:
    now = time.monotonic()
    with _dataset_cache_lock:
        cached = _dataset_cache.get(dataset_id)
        if cached is None:
            return None

        created_at, dataframe = cached
        if now - created_at >= DATASET_CACHE_TTL_SECONDS:
            del _dataset_cache[dataset_id]
            return None

        _dataset_cache.move_to_end(dataset_id)
        return dataframe.copy(deep=True)


def _normalize_header(value: Any) -> str:
    return re.sub(r"[\s_-]+", "_", str(value).strip().casefold())


def _build_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for canonical, names in CANONICAL_COLUMN_ALIASES.items():
        for name in (canonical, *names):
            normalized = _normalize_header(name)
            existing = aliases.get(normalized)
            if existing is not None and existing != canonical:
                raise RuntimeError(
                    f"Column alias {name!r} maps to multiple fields."
                )
            aliases[normalized] = canonical
    return aliases


_ALIAS_TO_CANONICAL = _build_alias_map()


def _original_source_column_name(dataframe: pd.DataFrame) -> str:
    column = "source_file_original"
    while column in dataframe.columns:
        column += "_"
    return column


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _is_blank(value: Any) -> bool:
    return _is_missing(value) or (
        isinstance(value, str) and value.strip() == ""
    )


def _parse_numeric(value: Any) -> tuple[float | int | None, str | None]:
    if _is_blank(value):
        return None, None
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None, "invalid"
        number: float | int = value
    else:
        text = str(value).strip()
        if not _THOUSANDS_NUMBER.fullmatch(text):
            return None, "invalid"
        try:
            number = float(text.replace(",", ""))
        except ValueError:
            return None, "invalid"
        if not math.isfinite(number):
            return None, "invalid"

    if number < 0:
        return number, "negative"
    return number, None


def _parse_date(value: Any) -> tuple[Any, bool]:
    if _is_blank(value):
        return value, False
    if isinstance(value, (pd.Timestamp,)):
        return value, False
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return pd.Timestamp(value), False
    if not isinstance(value, str):
        return value, True

    text = value.strip()
    try:
        return pd.Timestamp(
            pd.to_datetime(text, format="ISO8601", errors="raise")
        ), False
    except (TypeError, ValueError):
        pass
    for date_format in _ISO_DATE_FORMATS:
        try:
            return pd.Timestamp(
                pd.to_datetime(text, format=date_format, errors="raise")
            ), False
        except (TypeError, ValueError):
            continue
    return value, True


def _normalize_columns(
    dataframe: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    renamed: dict[Any, str] = {}
    canonical_sources: dict[str, list[Any]] = {}
    seen_headers: set[str] = set()

    for original in dataframe.columns:
        normalized_header = _normalize_header(original)
        if normalized_header in seen_headers:
            raise ValueError(
                f"Tên cột trùng lặp sau chuẩn hóa: {original!s}"
            )
        seen_headers.add(normalized_header)

        canonical = _ALIAS_TO_CANONICAL.get(normalized_header)
        normalized_name = canonical or str(original).strip()
        renamed[original] = normalized_name
        if canonical is not None:
            canonical_sources.setdefault(canonical, []).append(original)

    collisions = [
        canonical
        for canonical, originals in canonical_sources.items()
        if len(originals) > 1
    ]
    if collisions:
        raise ValueError(
            "Nhiều cột ánh xạ tới cùng một cột chuẩn: "
            + ", ".join(sorted(collisions))
        )

    normalized = dataframe.rename(columns=renamed).copy()
    source_columns = {
        canonical: str(originals[0])
        for canonical, originals in canonical_sources.items()
    }
    missing_required_columns = [
        column for column in REQUIRED_COLUMNS if column not in normalized.columns
    ]
    return normalized, source_columns, missing_required_columns


def _normalize_one_file(
    filename: str,
    dataframe: pd.DataFrame,
) -> PreparedHardwareFile:
    if dataframe.empty:
        return PreparedHardwareFile(
            filename=filename,
            dataframe=None,
            report={
                "filename": filename,
                "status": "failed",
                "validation_status": "failed",
                "rows_read": 0,
                "missing_required_columns": list(REQUIRED_COLUMNS),
                "error": "File không chứa bản ghi dữ liệu.",
            },
        )

    try:
        normalized, source_columns, missing_required = _normalize_columns(
            dataframe
        )
    except ValueError as error:
        return PreparedHardwareFile(
            filename=filename,
            dataframe=None,
            report={
                "filename": filename,
                "status": "failed",
                "validation_status": "failed",
                "rows_read": len(dataframe),
                "missing_required_columns": [],
                "error": str(error),
            },
        )

    rows_read = len(normalized)
    missing_values: dict[str, int] = {}
    invalid_numeric_values: dict[str, int] = {}
    negative_values: dict[str, int] = {}
    numeric_valid_masks: dict[str, list[bool]] = {}
    warning_row_flags = [False] * rows_read
    invalid_dates = 0
    date_valid_rows = [False] * rows_read
    issue_samples: list[dict[str, Any]] = []
    has_warnings = False

    for column in CANONICAL_COLUMN_ALIASES:
        if column not in normalized.columns:
            missing_values[column] = rows_read
        else:
            missing_values[column] = int(
                normalized[column].map(_is_blank).sum()
            )

    for column in NUMERIC_COLUMNS:
        invalid_count = 0
        negative_count = 0
        valid_rows: list[bool] = []
        if column in normalized.columns:
            converted_values = []
            for row_position, (row_index, value) in enumerate(
                normalized[column].items()
            ):
                parsed, issue = _parse_numeric(value)
                is_missing = _is_blank(value)
                valid_rows.append(parsed is not None and issue is None)
                if is_missing or issue in {"invalid", "negative"}:
                    warning_row_flags[row_position] = True
                if issue == "invalid":
                    invalid_count += 1
                    converted_values.append(value)
                    if len(issue_samples) < MAX_ISSUE_SAMPLES:
                        issue_samples.append(
                            {
                                "row": int(row_index) + 2,
                                "column": column,
                                "value": str(value),
                                "issue": "invalid_numeric",
                            }
                        )
                elif issue == "negative":
                    negative_count += 1
                    converted_values.append(parsed)
                    if len(issue_samples) < MAX_ISSUE_SAMPLES:
                        issue_samples.append(
                            {
                                "row": int(row_index) + 2,
                                "column": column,
                                "value": str(value),
                                "issue": "negative_value",
                            }
                        )
                elif parsed is None:
                    converted_values.append(value)
                else:
                    converted_values.append(parsed)
            normalized[column] = pd.Series(
                converted_values,
                index=normalized.index,
                dtype=object,
            )
            has_warnings |= bool(
                invalid_count
                or negative_count
                or missing_values.get(column, 0)
            )
        else:
            valid_rows = [False] * rows_read
        numeric_valid_masks[column] = valid_rows
        invalid_numeric_values[column] = invalid_count
        negative_values[column] = negative_count
        has_warnings |= bool(invalid_count or negative_count)

    if "Date" in normalized.columns:
        normalized_dates = []
        valid_dates = []
        for row_position, (row_index, value) in enumerate(
            normalized["Date"].items()
        ):
            parsed, invalid = _parse_date(value)
            normalized_dates.append(parsed)
            is_missing = _is_blank(value)
            valid_dates.append(not is_missing and not invalid)
            if is_missing or invalid:
                warning_row_flags[row_position] = True
                has_warnings = True
            if invalid:
                invalid_dates += 1
                has_warnings = True
                if len(issue_samples) < MAX_ISSUE_SAMPLES:
                    issue_samples.append(
                        {
                            "row": int(row_index) + 2,
                            "column": "Date",
                            "value": str(value),
                            "issue": "invalid_date_or_ambiguous_format",
                        }
                    )
        normalized["Date"] = pd.Series(
            normalized_dates,
            index=normalized.index,
            dtype=object,
        )
        date_valid_rows = valid_dates

    production_valid = numeric_valid_masks["Production"]
    defects_valid = numeric_valid_masks["Defects"]
    downtime_valid = numeric_valid_masks["Downtime"]
    defect_rate_valid = sum(
        production_is_valid and defects_is_valid
        for production_is_valid, defects_is_valid in zip(
            production_valid,
            defects_valid,
        )
    )
    rows_valid_for_kpi = {
        "production": sum(production_valid),
        "defects": sum(defects_valid),
        "defect_rate": defect_rate_valid,
        "downtime": sum(downtime_valid),
    }
    rows_excluded_from_analysis = {
        metric: rows_read - valid_rows
        for metric, valid_rows in rows_valid_for_kpi.items()
    }
    rows_excluded_from_analysis["time_series"] = (
        rows_read - sum(date_valid_rows)
    )

    missing_required_values = {
        column: missing_values.get(column, rows_read)
        for column in REQUIRED_COLUMNS
        if column in normalized.columns
        and missing_values.get(column, 0) > 0
    }
    has_warnings |= bool(missing_required_values)
    if missing_required:
        status = "failed"
    elif has_warnings:
        status = "warning"
    else:
        status = "valid"

    source_column = "_source_file"
    while source_column in normalized.columns:
        source_column += "_"
    normalized[source_column] = filename

    report: dict[str, Any] = {
        "filename": filename,
        "status": status,
        "validation_status": status,
        "rows_read": rows_read,
        "rows_with_warnings": sum(warning_row_flags),
        "rows_valid_for_kpi": rows_valid_for_kpi,
        "rows_excluded_from_analysis": rows_excluded_from_analysis,
        "missing_required_columns": missing_required,
        "missing_optional_columns": [
            column
            for column in OPTIONAL_COLUMNS
            if column not in normalized.columns
        ],
        "source_column": source_column,
        "column_mapping": {
            str(original): canonical
            for canonical, original in source_columns.items()
            if original != canonical
        },
        "missing_values": missing_values,
        "missing_required_values": missing_required_values,
        "invalid_dates": invalid_dates,
        "invalid_numeric_values": sum(invalid_numeric_values.values()),
        "invalid_numeric_values_by_column": invalid_numeric_values,
        "negative_values": sum(negative_values.values()),
        "negative_values_by_column": negative_values,
        "issue_samples": issue_samples,
    }
    if missing_required:
        report["error"] = (
            "Thiếu cột bắt buộc: " + ", ".join(missing_required)
        )

    return PreparedHardwareFile(
        filename=filename,
        dataframe=normalized,
        report=report,
    )


def normalize_hardware_files(
    files: list[dict[str, Any]],
) -> MultiFileNormalization:
    prepared_files: list[PreparedHardwareFile] = []
    for item in files:
        filename = str(item.get("filename") or "File không tên")
        dataframe = item.get("dataframe")
        error = item.get("error")
        if error is not None:
            prepared_files.append(
                PreparedHardwareFile(
                    filename=filename,
                    dataframe=None,
                    report={
                        "filename": filename,
                        "status": "failed",
                        "validation_status": "failed",
                        "rows_read": 0,
                        "missing_required_columns": [],
                        "error": str(error),
                    },
                )
            )
        elif isinstance(dataframe, pd.DataFrame):
            prepared_files.append(
                _normalize_one_file(filename, dataframe)
            )
        else:
            prepared_files.append(
                PreparedHardwareFile(
                    filename=filename,
                    dataframe=None,
                    report={
                        "filename": filename,
                        "status": "failed",
                        "validation_status": "failed",
                        "rows_read": 0,
                        "missing_required_columns": [],
                        "error": "Không có dữ liệu DataFrame để kiểm tra.",
                    },
                )
            )

    file_reports = [item.report for item in prepared_files]
    files_valid = sum(report["status"] == "valid" for report in file_reports)
    files_with_warnings = sum(
        report["status"] == "warning" for report in file_reports
    )
    files_failed = sum(report["status"] == "failed" for report in file_reports)

    if files_failed == len(prepared_files):
        status = "failed"
    elif files_with_warnings or files_failed:
        status = "completed_with_warnings"
    else:
        status = "completed"

    return MultiFileNormalization(
        report={
            "status": status,
            "files_processed": len(prepared_files),
            "files_valid": files_valid,
            "files_with_warnings": files_with_warnings,
            "files_failed": files_failed,
            "required_columns": list(REQUIRED_COLUMNS),
            "optional_schema_columns": list(OPTIONAL_COLUMNS),
            "file_reports": file_reports,
        },
        files=prepared_files,
    )


def merge_normalized_hardware_files(
    normalization: MultiFileNormalization,
) -> MergedHardwareDataset:
    mergeable_files = [
        item
        for item in normalization.files
        if item.dataframe is not None
        and item.report["status"] in {"valid", "warning"}
    ]
    skipped_files = [
        item
        for item in normalization.files
        if item.dataframe is None
        or item.report["status"] not in {"valid", "warning"}
    ]

    extra_columns = sorted(
        {
            str(column)
            for item in mergeable_files
            for column in item.dataframe.columns
            if column not in CANONICAL_COLUMN_ALIASES
            and column != item.report.get("source_column")
            and column != "source_file"
        },
        key=str.casefold,
    )
    if any(
        "source_file" in item.dataframe.columns
        for item in mergeable_files
    ):
        extra_columns.append("source_file_original")
        extra_columns = sorted(set(extra_columns), key=str.casefold)

    standardized_columns = [
        *CANONICAL_COLUMN_ALIASES.keys(),
        *extra_columns,
        "source_file",
    ]
    merged_frames: list[pd.DataFrame] = []
    merged_file_indexes: list[int] = []
    file_reports: list[dict[str, Any]] = []
    source_file_rename_warning = False
    total_rows_before_merge = 0
    total_rows_skipped = 0
    rows_with_warnings = 0
    rows_valid_for_kpi = {
        "production": 0,
        "defects": 0,
        "defect_rate": 0,
        "downtime": 0,
    }
    rows_excluded_from_analysis = {
        "production": 0,
        "defects": 0,
        "defect_rate": 0,
        "downtime": 0,
        "time_series": 0,
    }

    for file_index, item in enumerate(normalization.files):
        report = dict(item.report)
        dataframe = item.dataframe

        if (
            dataframe is None
            or report["status"] not in {"valid", "warning"}
        ):
            report["status"] = "skipped"
            report["rows_merged"] = 0
            report["rows_skipped"] = report["rows_read"]
            total_rows_skipped += report["rows_read"]
            file_reports.append(report)
            continue

        source_column = report["source_column"]
        prepared = dataframe.copy()
        file_has_source_conflict = "source_file" in prepared.columns
        if "source_file" in prepared.columns:
            original_source_column = _original_source_column_name(prepared)
            prepared = prepared.rename(
                columns={"source_file": original_source_column}
            )
            report["source_columns_renamed"] = {
                "source_file": original_source_column
            }
            report["warnings"] = [
                *report.get("warnings", []),
                "Cột source_file gốc được giữ dưới tên "
                f"{original_source_column}; source_file mới ghi tên file upload.",
            ]
            source_file_rename_warning = True

        if source_column not in prepared.columns:
            raise ValueError(
                f"Không tìm thấy cột truy vết nguồn của file {item.filename!r}."
            )

        prepared = prepared.rename(
            columns={source_column: "source_file"}
        )
        for column in standardized_columns:
            if column not in prepared.columns:
                prepared[column] = pd.NA
        prepared = prepared.loc[:, standardized_columns]

        rows_merged = len(prepared)
        total_rows_before_merge += rows_merged
        merged_file_indexes.extend([file_index] * rows_merged)
        report["rows_merged"] = rows_merged
        report["rows_skipped"] = 0
        rows_with_warnings += report.get("rows_with_warnings", 0)
        for metric, count in report.get("rows_valid_for_kpi", {}).items():
            rows_valid_for_kpi[metric] += count
        for analysis, count in report.get(
            "rows_excluded_from_analysis",
            {},
        ).items():
            rows_excluded_from_analysis[analysis] += count
        report["status"] = (
            "merged_with_warnings"
            if item.report["status"] == "warning"
            or file_has_source_conflict
            else "merged"
        )
        merged_frames.append(prepared)
        file_reports.append(report)

    if merged_frames:
        merged_dataframe = pd.concat(
            merged_frames,
            ignore_index=True,
            sort=False,
        ).loc[:, standardized_columns]
    else:
        merged_dataframe = pd.DataFrame(columns=standardized_columns)

    duplicate_mask = merged_dataframe.duplicated(
        subset=[
            column
            for column in standardized_columns
            if column != "source_file"
        ],
        keep="first",
    )
    duplicate_rows_detected = int(duplicate_mask.sum())
    duplicate_rows_by_file: dict[int, int] = {}
    for file_index, is_duplicate in zip(merged_file_indexes, duplicate_mask):
        if is_duplicate:
            duplicate_rows_by_file[file_index] = (
                duplicate_rows_by_file.get(file_index, 0) + 1
            )
    for file_index, report in enumerate(file_reports):
        duplicates_in_file = duplicate_rows_by_file.get(file_index, 0)
        report["duplicate_rows_detected"] = duplicates_in_file
        if duplicates_in_file and report["status"] == "merged":
            report["status"] = "merged_with_warnings"

    files_merged = len(mergeable_files)
    files_skipped = len(skipped_files)
    total_rows_after_merge = len(merged_dataframe)

    if not files_merged:
        status = "failed"
    elif (
        normalization.report["files_with_warnings"]
        or files_skipped
        or duplicate_rows_detected
        or source_file_rename_warning
    ):
        status = "completed_with_warnings"
    else:
        status = "completed"

    return MergedHardwareDataset(
        report={
            "status": status,
            "files_received": len(normalization.files),
            "files_merged": files_merged,
            "files_skipped": files_skipped,
            "total_rows_before_merge": total_rows_before_merge,
            "total_rows_after_merge": total_rows_after_merge,
            "rows_merged": total_rows_after_merge,
            "rows_skipped": total_rows_skipped,
            "rows_with_warnings": rows_with_warnings,
            "rows_valid_for_kpi": rows_valid_for_kpi,
            "rows_excluded_from_analysis": rows_excluded_from_analysis,
            "duplicate_rows_detected": duplicate_rows_detected,
            "duplicate_policy": (
                "Phát hiện dòng trùng chính xác trên các cột chuẩn hóa "
                "và cột bổ sung; không tự động loại bỏ."
            ),
            "schema_columns": standardized_columns,
            "file_reports": file_reports,
        },
        dataframe=merged_dataframe,
    )
