import csv
import io
import logging
import math
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd
from fastapi.encoders import jsonable_encoder
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.analysis_history_service import create_analysis_history
from app.services.hardware_ai_service import (
    analyze_hardware_comparison,
    analyze_hardware_trends_with_ai,
)
from app.services.hardware_service import (
    analyze_hardware_dataframe,
    compare_hardware_models,
)
from app.services.hardware_comparison_service import (
    ComparisonDimension,
    compare_hardware_periods,
)
from app.services.hardware_report_service import (
    build_hardware_pdf,
    build_hardware_xlsx,
)
from app.services.multi_file_service import (
    DATASET_CACHE_TTL_SECONDS,
    get_merged_hardware_dataset,
    merge_normalized_hardware_files,
    normalize_hardware_files,
    store_merged_hardware_dataset,
)
from app.services.hardware_trend_service import (
    Granularity,
    analyze_hardware_trends,
)


router = APIRouter(
    prefix="/api/hardware",
    tags=["HardwareAI - Hardware Analysis"],
)

logger = logging.getLogger(__name__)
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_MULTIPLE_FILES = 10
AI_UNAVAILABLE_MESSAGE = (
    "Dịch vụ AI hiện tạm thời không khả dụng. "
    "Các chỉ số so sánh được tính bằng Python vẫn có thể sử dụng."
)
TREND_AI_UNAVAILABLE_MESSAGE = (
    "Dịch vụ AI hiện tạm thời không khả dụng. "
    "Kết quả thống kê xu hướng do Python tính vẫn có thể sử dụng."
)


class HardwareTrendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    granularity: Granularity
    start_date: date | None = None
    end_date: date | None = None


def _json_safe_cell(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return _json_safe_cell(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _read_csv_robust(content: bytes) -> pd.DataFrame:
    if not content:
        raise ValueError("File CSV không chứa dữ liệu.")

    encodings = (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "cp1258",
        "latin-1",
    )
    delimiters = (";", ",", "\t", "|")
    last_error: Exception | None = None

    for encoding in encodings:
        try:
            text = content.decode(encoding, errors="strict")
        except UnicodeDecodeError as error:
            last_error = error
            continue

        text = text.lstrip("\ufeff")
        if not text.strip():
            raise ValueError("File CSV không chứa dữ liệu.")

        lines = text.splitlines()
        nonempty_lines = [line for line in lines if line.strip()]
        if nonempty_lines and all(
            line.strip().startswith('"') and line.strip().endswith('"')
            for line in nonempty_lines
        ):
            text = "\n".join(
                line.strip()[1:-1] if line.strip() else line
                for line in lines
            )

        first_line = next(
            (line for line in text.splitlines() if line.strip()),
            "",
        )
        if not first_line:
            raise ValueError("File CSV không chứa dòng tiêu đề.")

        ordered_delimiters = sorted(
            delimiters,
            key=first_line.count,
            reverse=True,
        )

        for delimiter in ordered_delimiters:
            if first_line.count(delimiter) == 0:
                continue

            for quoting in (csv.QUOTE_MINIMAL, csv.QUOTE_NONE):
                try:
                    dataframe = pd.read_csv(
                        io.StringIO(text),
                        sep=delimiter,
                        engine="python",
                        dtype=object,
                        quoting=quoting,
                    )
                except (pd.errors.ParserError, ValueError) as error:
                    last_error = error
                    continue

                dataframe.columns = [
                    str(column).strip().strip('"').lstrip("\ufeff")
                    for column in dataframe.columns
                ]
                if len(dataframe.columns) > 1:
                    return dataframe

        if not any(first_line.count(delimiter) for delimiter in delimiters):
            try:
                dataframe = pd.read_csv(
                    io.StringIO(text),
                    dtype=object,
                    engine="python",
                )
            except (pd.errors.ParserError, ValueError) as error:
                last_error = error
                continue

            dataframe.columns = [
                str(column).strip().strip('"').lstrip("\ufeff")
                for column in dataframe.columns
            ]
            return dataframe

    if last_error is not None:
        raise ValueError(
            f"Không thể đọc CSV: {last_error}"
        ) from last_error
    raise ValueError(
        "Không thể đọc file CSV với các encoding được hỗ trợ."
    )


async def _read_hardware_upload(file: UploadFile) -> pd.DataFrame:
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Vui lòng chọn file dữ liệu.",
        )

    extension = "." + file.filename.split(".")[-1].lower()
    if extension not in [".csv", ".xlsx"]:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ file CSV hoặc XLSX.",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail="File không được vượt quá 10MB.",
        )

    if not content:
        raise HTTPException(
            status_code=400,
            detail="File tải lên đang trống.",
        )

    if extension == ".csv":
        logger.warning(
            "CSV RAW | filename=%s | size=%d | first_bytes=%r",
            file.filename,
            len(content),
            content[:120],
        )
        logger.warning(
            "CSV TEXT | filename=%s | preview=%r",
            file.filename,
            content[:500].decode(
                "utf-8",
                errors="backslashreplace",
            ),
        )
        dataframe = _read_csv_robust(content)
        logger.warning(
            "CSV DEBUG | filename=%s | columns=%s | shape=%s",
            file.filename,
            dataframe.columns.tolist(),
            dataframe.shape,
        )
    else:
        excel = pd.ExcelFile(
            io.BytesIO(content),
            engine="openpyxl",
        )
        frames = []
        for sheet_name in excel.sheet_names:
            sheet_dataframe = pd.read_excel(
                excel,
                sheet_name=sheet_name,
                engine="openpyxl",
            )
            if sheet_dataframe.empty:
                continue
            sheet_dataframe["_source_sheet"] = sheet_name
            frames.append(sheet_dataframe)

        if not frames:
            raise ValueError("File Excel không chứa dữ liệu.")

        dataframe = pd.concat(frames, ignore_index=True)

    if dataframe.empty:
        raise HTTPException(
            status_code=400,
            detail="File không chứa dữ liệu.",
        )

    return dataframe


def _save_hardware_history_safely(
    db: Session,
    *,
    analysis_type: str,
    title: str,
    result_data: dict[str, Any],
    source_filename: str | None = None,
    dataset_id: str | None = None,
) -> None:
    """Save analysis history without breaking a successful analysis response."""
    try:
        encoded_result = jsonable_encoder(result_data)
        summary_value = encoded_result.get("summary")
        summary = (
            summary_value
            if isinstance(summary_value, str)
            else None
        )
        create_analysis_history(
            db,
            analysis_type=analysis_type,
            title=title,
            summary=summary,
            source_filename=source_filename,
            dataset_id=dataset_id,
            result_data=encoded_result,
        )
    except Exception:
        logger.exception(
            "Could not save hardware analysis history: %s",
            analysis_type,
        )


@router.post("/analyze")
async def analyze_hardware(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        dataframe = await _read_hardware_upload(file)
        response = analyze_hardware_dataframe(dataframe)
        _save_hardware_history_safely(
            db,
            analysis_type="hardware_analysis",
            title="Phân tích dữ liệu phần cứng",
            source_filename=file.filename,
            result_data=response,
        )
        return {
            **response,
            "data_quality": response["data_quality"],
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Hardware analysis failed")
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@router.post("/compare")
async def compare_hardware(
    file: UploadFile = File(...),
    model_a: str = Form(...),
    model_b: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        dataframe = await _read_hardware_upload(file)
        try:
            comparison = compare_hardware_models(
                dataframe,
                model_a,
                model_b,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

        try:
            comparison["ai_analysis"] = analyze_hardware_comparison(
                comparison
            )
            comparison["ai_status"] = "available"
        except Exception:
            logger.exception("Hardware comparison AI analysis failed")
            comparison["ai_analysis"] = None
            comparison["ai_status"] = "unavailable"
            comparison["ai_error"] = AI_UNAVAILABLE_MESSAGE

        _save_hardware_history_safely(
            db,
            analysis_type="hardware_comparison",
            title=f"So sánh phần cứng: {model_a} và {model_b}",
            source_filename=file.filename,
            result_data=comparison,
        )
        return comparison
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Hardware comparison failed")
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@router.post("/compare-periods")
async def compare_hardware_periods_endpoint(
    file: UploadFile = File(...),
    period_a_start: date = Form(...),
    period_a_end: date = Form(...),
    period_b_start: date = Form(...),
    period_b_end: date = Form(...),
    dimension: ComparisonDimension | None = Form(default=None),
    db: Session = Depends(get_db),
):
    try:
        dataframe = await _read_hardware_upload(file)
        try:
            comparison = compare_hardware_periods(
                dataframe,
                period_a_start=period_a_start,
                period_a_end=period_a_end,
                period_b_start=period_b_start,
                period_b_end=period_b_end,
                dimension=dimension,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

        dimension_label = f" theo {dimension}" if dimension else ""
        _save_hardware_history_safely(
            db,
            analysis_type="hardware_period_comparison",
            title=f"So sánh hai khoảng thời gian{dimension_label}",
            source_filename=file.filename,
            result_data=comparison,
        )
        return comparison
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Hardware period comparison failed")
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@router.post("/export")
def export_hardware_report(
    payload: dict[str, Any] = Body(...),
    file_format: Literal["pdf", "xlsx"] = Query(
        default="pdf",
        alias="format",
    ),
):
    if not payload:
        raise HTTPException(
            status_code=422,
            detail="Không có dữ liệu phân tích để xuất báo cáo.",
        )
    if not any(
        key in payload
        for key in (
            "title",
            "summary",
            "kpis",
            "daily_production",
            "ai_analysis",
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="Payload không có cấu trúc kết quả phân tích HardwareAI.",
        )

    if file_format == "pdf":
        try:
            content = build_hardware_pdf(payload)
        except Exception as error:
            logger.exception("HardwareAI PDF report generation failed")
            raise HTTPException(
                status_code=500,
                detail="Không thể tạo báo cáo PDF.",
            ) from error
        media_type = "application/pdf"
        filename = "HardwareAI_Report.pdf"
    else:
        try:
            content = build_hardware_xlsx(payload)
        except Exception as error:
            logger.exception("HardwareAI Excel report generation failed")
            raise HTTPException(
                status_code=500,
                detail="Không thể tạo báo cáo Excel.",
            ) from error
        media_type = (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
        filename = "HardwareAI_Report.xlsx"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/analyze-multiple")
async def analyze_hardware_multiple(
    files: list[UploadFile] = File(
        ...,
        description="Upload 2–10 file CSV hoặc XLSX.",
        json_schema_extra={
            "items": {
                "type": "string",
                "format": "binary",
            }
        },
    ),
):
    """Normalize, validate, and merge uploaded hardware files."""
    if len(files) < 2:
        raise HTTPException(
            status_code=400,
            detail="Vui lòng tải lên ít nhất hai file dữ liệu.",
        )

    if len(files) > MAX_MULTIPLE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ hỗ trợ tối đa {MAX_MULTIPLE_FILES} file mỗi lần.",
        )

    upload_results = []

    for file in files:
        filename = file.filename or "File không tên"
        try:
            dataframe = await _read_hardware_upload(file)
        except HTTPException as error:
            upload_results.append(
                {
                    "filename": filename,
                    "error": error.detail,
                }
            )
        except Exception as error:
            logger.exception(
                "Could not read uploaded hardware file %r",
                file.filename,
            )
            upload_results.append(
                {
                    "filename": filename,
                    "error": f"Không thể đọc file: {error}",
                }
            )
        else:
            upload_results.append(
                {
                    "filename": filename,
                    "dataframe": dataframe,
                }
            )

    result = normalize_hardware_files(upload_results)
    merged = merge_normalized_hardware_files(result)
    dataset_id = (
        store_merged_hardware_dataset(merged.dataframe)
        if merged.report["files_merged"] > 0
        else None
    )
    normalized_files = []
    for prepared_file, file_report in zip(
        result.files,
        merged.report["file_reports"],
    ):
        dataframe = prepared_file.dataframe
        preview = []
        if dataframe is not None:
            for row in dataframe.head(5).to_dict(orient="records"):
                preview_row = {}
                for column, value in row.items():
                    preview_row[column] = _json_safe_cell(value)
                preview.append(preview_row)

        normalized_files.append(
            {
                "filename": prepared_file.filename,
                "status": file_report["status"],
                "source_column": prepared_file.report.get("source_column"),
                "row_count": prepared_file.report["rows_read"],
                "columns": dataframe.columns.tolist()
                if dataframe is not None
                else [],
                "preview": preview,
            }
        )

    return {
        **result.report,
        **merged.report,
        "file_count": len(result.files),
        "dataset_id": dataset_id,
        "dataset_cache_ttl_seconds": (
            DATASET_CACHE_TTL_SECONDS if dataset_id else None
        ),
        "total_rows": sum(
            item.report["rows_read"] for item in result.files
        ),
        "merged_rows": [
            {
                column: _json_safe_cell(value)
                for column, value in row.items()
            }
            for row in merged.dataframe.to_dict(orient="records")
        ],
        "files": normalized_files,
    }


@router.post("/trends")
async def analyze_hardware_trend(
    request: HardwareTrendRequest,
    db: Session = Depends(get_db),
):
    dataframe = get_merged_hardware_dataset(request.dataset_id)
    if dataframe is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Không tìm thấy dataset hoặc dataset đã hết hạn. "
                "Vui lòng gộp lại các file để nhận dataset_id mới."
            ),
        )

    try:
        result = analyze_hardware_trends(
            dataframe,
            granularity=request.granularity,
            start_date=request.start_date,
            end_date=request.end_date,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    try:
        result["ai_analysis"] = analyze_hardware_trends_with_ai(result)
        result["ai_status"] = "available"
    except Exception as exc:
        logger.exception(
            "Hardware trend AI analysis failed: %s",
            str(exc),
        )
        if hasattr(exc, "message"):
            logger.error(
                "Gemini API error detail: %s",
                exc.message,
            )
        result["ai_analysis"] = None
        result["ai_status"] = "unavailable"
        result["ai_error"] = TREND_AI_UNAVAILABLE_MESSAGE

    _save_hardware_history_safely(
        db,
        analysis_type="hardware_trends",
        title="Phân tích xu hướng phần cứng",
        dataset_id=request.dataset_id,
        result_data=result,
    )
    return result
