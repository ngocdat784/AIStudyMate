import csv
import hashlib
import io
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
import re
from typing import Any
from uuid import UUID
import zipfile

import pandas as pd
from docx import Document
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
    status,
)
from pypdf import PdfReader
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database.connection import SessionLocal, get_db
from app.models import AnalysisHistory, HardwareAnalysisJob
from app.models.monitoring import AlertUser, Plant
from app.services.monitoring_auth import (
    get_current_user,
    require_plant_access,
    require_roles,
)
from app.services.api_rate_limit import rate_limit_dependency
from app.services.analysis_history_service import create_analysis_history
from app.services.hardware_ai_service import (
    analyze_hardware_comparison,
    explain_hardware_intervention,
    analyze_hardware_trends_with_ai,
)
from app.services.hardware_service import (
    analyze_hardware_dataframe,
    compare_hardware_models,
    find_column,
    generate_custom_hardware_response,
)
from app.services.hardware_comparison_service import (
    ComparisonDimension,
    compare_hardware_periods,
)
from app.services.multi_file_service import (
    DATASET_CACHE_TTL_SECONDS,
    DatasetCacheCapacityError,
    get_merged_hardware_dataset,
    merge_normalized_hardware_files,
    normalize_hardware_files,
    store_merged_hardware_dataset,
)
from app.services.hardware_trend_service import (
    Granularity,
    analyze_hardware_trends,
)
from app.services.hardware_intervention_service import (
    evaluate_hardware_intervention,
)
from app.services.hardware_analysis_jobs import (
    enqueue_hardware_analysis_job,
    fail_hardware_analysis_job,
    serialize_hardware_analysis_job,
    update_hardware_analysis_job_stage,
)


router = APIRouter(
    prefix="/api/hardware",
    tags=["HardwareAI - Hardware Analysis"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)

logger = logging.getLogger(__name__)
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_MULTIPLE_FILES = 10
MAX_MULTIPLE_TOTAL_SIZE = 25 * 1024 * 1024
MAX_HARDWARE_ROWS = 100_000
MAX_HARDWARE_COLUMNS = 500
MAX_ARCHIVE_ENTRIES = 1_000
MAX_ARCHIVE_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_MULTIPLE_TOTAL_ROWS = 100_000
MAX_MERGED_RESPONSE_ROWS = 1_000
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


class HardwareInterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    before_start_date: date
    before_end_date: date
    after_start_date: date
    after_end_date: date
    model: str | None = None


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

        first_line = ""
        all_lines_wrapped = True
        has_nonempty_line = False
        for line in io.StringIO(text):
            stripped = line.strip()
            if not stripped:
                continue
            has_nonempty_line = True
            if not first_line:
                first_line = stripped
            if not (
                stripped.startswith('"')
                and stripped.endswith('"')
            ):
                all_lines_wrapped = False

        has_quoted_delimited_fields = any(
            first_line.count(delimiter) > 0
            and len(
                next(
                    csv.reader(
                        [first_line],
                        delimiter=delimiter,
                        quotechar='"',
                    )
                )
            ) > 1
            for delimiter in delimiters
        )
        if (
            has_nonempty_line
            and all_lines_wrapped
            and not has_quoted_delimited_fields
        ):
            text = "\n".join(
                line.strip()[1:-1] if line.strip() else line
                for line in io.StringIO(text)
            )

        if not first_line:
            raise ValueError("File CSV không chứa dòng tiêu đề.")
        if (
            max(first_line.count(delimiter) for delimiter in delimiters) + 1
            > MAX_HARDWARE_COLUMNS
        ):
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_HARDWARE_COLUMNS} cột.",
            )

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
                        nrows=MAX_HARDWARE_ROWS + 1,
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
                    nrows=MAX_HARDWARE_ROWS + 1,
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
            "Không thể đọc CSV với encoding hoặc định dạng được hỗ trợ."
        ) from last_error
    raise ValueError(
        "Không thể đọc file CSV với các encoding được hỗ trợ."
    )


def _has_production_column(columns: list[str]) -> bool:
    return find_column(
        pd.DataFrame(columns=columns),
        "production",
    ) is not None


def _read_docx_hardware_tables(content: bytes) -> pd.DataFrame:
    try:
        document = Document(io.BytesIO(content))
    except Exception as error:
        raise ValueError("Không thể đọc file DOCX.") from error

    frames = []
    for table in document.tables:
        rows = [
            [cell.text.strip() for cell in row.cells]
            for row in table.rows
        ]
        if len(rows) < 2:
            continue

        headers = rows[0]
        if not _has_production_column(headers):
            continue

        dataframe = pd.DataFrame(
            rows[1:],
            columns=headers,
        ).dropna(how="all")
        if not dataframe.empty:
            frames.append(dataframe)

    if not frames:
        raise ValueError(
            "Không tìm thấy bảng DOCX có cột sản lượng."
        )

    return pd.concat(frames, ignore_index=True, sort=False)


def _split_pdf_table_line(line: str) -> list[str]:
    if "|" in line:
        cells = re.split(r"\s*\|\s*", line.strip().strip("|"))
    elif "\t" in line:
        cells = line.split("\t")
    else:
        cells = re.split(r"\s{2,}", line.strip())
    return [cell.strip() for cell in cells]


def _read_pdf_hardware_tables(content: bytes) -> pd.DataFrame:
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError("Không thể đọc file PDF.") from error

    if len(reader.pages) > MAX_PDF_PAGES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF vượt quá giới hạn {MAX_PDF_PAGES} trang.",
        )

    frames = []
    for page in reader.pages:
        try:
            page_text = page.extract_text(extraction_mode="layout") or ""
        except Exception as error:
            raise ValueError("Không thể trích xuất bảng từ PDF.") from error

        headers = None
        rows = []
        for line in page_text.splitlines():
            cells = _split_pdf_table_line(line)
            if len(cells) > 1 and _has_production_column(cells):
                if headers and rows:
                    frames.append(
                        pd.DataFrame(rows, columns=headers)
                    )
                headers = cells
                rows = []
                continue

            if headers is None:
                continue

            if len(cells) == len(headers) and any(cells):
                if cells != headers:
                    rows.append(cells)
                continue

            if rows:
                frames.append(pd.DataFrame(rows, columns=headers))
            headers = None
            rows = []

        if headers and rows:
            frames.append(pd.DataFrame(rows, columns=headers))

    if not frames:
        raise ValueError(
            "Không tìm thấy bảng dữ liệu phần cứng có thể đọc trong PDF. "
            "PDF scan/ảnh cần được chuyển thành PDF có văn bản trước khi tải lên."
        )

    dataframe = pd.concat(frames, ignore_index=True, sort=False)
    dataframe = dataframe.dropna(how="all")
    if dataframe.empty:
        raise ValueError("Bảng trong PDF không chứa dữ liệu.")
    return dataframe


def _validate_hardware_filename(
    filename: str | None,
    *,
    allow_documents: bool = False,
) -> str:
    if not filename:
        raise HTTPException(
            status_code=400,
            detail="Vui lòng chọn file dữ liệu.",
        )

    extension = Path(filename).suffix.lower()
    supported_extensions = {".csv", ".xlsx"}
    if allow_documents:
        supported_extensions.update({".pdf", ".docx"})
    if extension not in supported_extensions:
        supported_formats = (
            "CSV, XLSX, PDF có bảng văn bản hoặc DOCX"
            if allow_documents
            else "CSV hoặc XLSX"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ hỗ trợ file {supported_formats}.",
        )

    return extension


async def _read_upload_content(file: UploadFile) -> bytes:
    return await _read_upload_content_with_limit(file, MAX_FILE_SIZE)


async def _read_upload_content_with_limit(
    file: UploadFile,
    max_file_size: int,
) -> bytes:
    content = bytearray()
    while chunk := await file.read(64 * 1024):
        if len(content) + len(chunk) > max_file_size:
            if max_file_size == MAX_FILE_SIZE:
                message = "File không được vượt quá 10MB."
            else:
                message = "Tổng dung lượng tải lên vượt quá giới hạn cho phép."
            raise HTTPException(
                status_code=413,
                detail=message,
            )
        content.extend(chunk)
    if not content:
        raise HTTPException(
            status_code=400,
            detail="File tải lên đang trống.",
        )
    return bytes(content)


def _parse_hardware_content(
    filename: str,
    content: bytes,
    *,
    allow_documents: bool = False,
) -> pd.DataFrame:
    extension = _validate_hardware_filename(
        filename,
        allow_documents=allow_documents,
    )
    if not content:
        raise ValueError("File tải lên đang trống.")
    if extension in {".xlsx", ".docx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("Tệp Office không hợp lệ.") from error
        if len(entries) > MAX_ARCHIVE_ENTRIES or sum(
            entry.file_size for entry in entries
        ) > MAX_ARCHIVE_UNCOMPRESSED_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Tệp Office vượt quá giới hạn giải nén cho phép.",
            )
        if any(entry.flag_bits & 0x1 for entry in entries):
            raise ValueError("Không hỗ trợ tệp Office được mã hóa.")

    if extension == ".csv":
        dataframe = _read_csv_robust(content)
        logger.info(
            "Parsed hardware CSV: filename=%s rows=%d columns=%d",
            Path(filename).name,
            len(dataframe.index),
            len(dataframe.columns),
        )
    elif extension == ".xlsx":
        excel = pd.ExcelFile(
            io.BytesIO(content),
            engine="openpyxl",
        )
        frames = []
        total_rows = 0
        for sheet_name in excel.sheet_names:
            sheet_dataframe = pd.read_excel(
                excel,
                sheet_name=sheet_name,
                engine="openpyxl",
                nrows=MAX_HARDWARE_ROWS + 1 - total_rows,
            )
            if sheet_dataframe.empty:
                continue
            sheet_dataframe["_source_sheet"] = sheet_name
            frames.append(sheet_dataframe)
            total_rows += len(sheet_dataframe.index)
            if total_rows > MAX_HARDWARE_ROWS:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File vượt quá giới hạn {MAX_HARDWARE_ROWS:,} dòng."
                    ),
                )

        if not frames:
            raise ValueError("File Excel không chứa dữ liệu.")

        dataframe = pd.concat(frames, ignore_index=True)
    elif extension == ".docx":
        try:
            dataframe = _read_docx_hardware_tables(content)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error
    else:
        try:
            dataframe = _read_pdf_hardware_tables(content)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

    if dataframe.empty:
        raise ValueError("File không chứa dữ liệu.")
    if len(dataframe.columns) > MAX_HARDWARE_COLUMNS:
        raise HTTPException(
            status_code=413,
            detail=f"File vượt quá giới hạn {MAX_HARDWARE_COLUMNS} cột.",
        )
    if len(dataframe.index) > MAX_HARDWARE_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"File vượt quá giới hạn {MAX_HARDWARE_ROWS:,} dòng.",
        )

    return dataframe


async def _read_hardware_upload(
    file: UploadFile,
    *,
    allow_documents: bool = False,
) -> pd.DataFrame:
    if not file.filename:
        _validate_hardware_filename(None, allow_documents=allow_documents)
    content = await _read_upload_content(file)
    return await run_in_threadpool(
        _parse_hardware_content,
        file.filename or "",
        content,
        allow_documents=allow_documents,
    )


def _save_hardware_history_safely(
    db: Session,
    *,
    creator_user_id: int,
    analysis_type: str,
    title: str,
    result_data: dict[str, Any],
    source_filename: str | None = None,
    dataset_id: str | None = None,
    source_file_sha256: str | None = None,
    plant_id: int | None = None,
) -> int | None:
    """Save analysis history without breaking a successful analysis response."""
    try:
        encoded_result = jsonable_encoder(result_data)
        summary_value = encoded_result.get("summary")
        summary = (
            summary_value
            if isinstance(summary_value, str)
            else None
        )
        history = create_analysis_history(
            db,
            analysis_type=analysis_type,
            title=title,
            summary=summary,
            source_filename=source_filename,
            dataset_id=dataset_id,
            plant_id=plant_id,
            source_file_sha256=source_file_sha256,
            algorithm_version="hardware-analysis-v1",
            ai_status=encoded_result.get("ai_status"),
            evidence=encoded_result.get("evidence"),
            result_data=encoded_result,
            creator_user_id=creator_user_id,
        )
        return history.id
    except Exception:
        logger.exception(
            "Could not save hardware analysis history: %s",
            analysis_type,
        )
        return None


def _upload_sha256(file: UploadFile) -> str:
    file.file.seek(0)
    digest = hashlib.sha256()
    while chunk := file.file.read(1024 * 1024):
        digest.update(chunk)
    file.file.seek(0)
    return digest.hexdigest()


@router.post(
    "/analyze",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "hardware-analysis",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_hardware(
    file: UploadFile = File(...),
    custom_prompt: str = Form(default="", max_length=2000),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    try:
        dataframe = await _read_hardware_upload(
            file,
            allow_documents=True,
        )
        response = await run_in_threadpool(
            analyze_hardware_dataframe,
            dataframe,
        )
        if custom_prompt.strip():
            try:
                response["custom_prompt_response"] = (
                    generate_custom_hardware_response(
                        dict(response),
                        custom_prompt,
                    )
                )
            except Exception:
                logger.exception(
                    "Hardware custom prompt response failed"
                )
                response["custom_prompt_error"] = (
                    "Không thể tạo phản hồi theo prompt riêng. "
                    "Kết quả phân tích chính vẫn được giữ nguyên."
                )
        _save_hardware_history_safely(
            db,
            creator_user_id=user.id,
            source_file_sha256=_upload_sha256(file),
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


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(
            rate_limit_dependency(
                "hardware-analysis-jobs",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def create_hardware_analysis_job(
    file: UploadFile = File(...),
    custom_prompt: str = Form(default="", max_length=2000),
    plant_id: int | None = Form(default=None, gt=0),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not settings.HARDWARE_ANALYSIS_JOBS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Phân tích nền hiện chưa được bật.",
        )

    _validate_hardware_filename(file.filename, allow_documents=True)
    if plant_id is not None:
        if db.get(Plant, plant_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy nhà máy.",
            )
        require_plant_access(db, user, plant_id)
    content = await _read_upload_content(file)
    job = enqueue_hardware_analysis_job(
        db,
        owner_user_id=user.id,
        filename=Path(file.filename or "").name,
        content=content,
        custom_prompt=custom_prompt.strip(),
        plant_id=plant_id,
    )
    return serialize_hardware_analysis_job(job, include_result=False)


@router.get("/jobs")
def list_hardware_analysis_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    statement = select(HardwareAnalysisJob)
    count_statement = select(func.count()).select_from(HardwareAnalysisJob)
    if user.role != "admin":
        owner_filter = HardwareAnalysisJob.owner_user_id == user.id
        statement = statement.where(owner_filter)
        count_statement = count_statement.where(owner_filter)

    total = db.scalar(count_statement) or 0
    jobs = db.scalars(
        statement
        .options(
            load_only(
                HardwareAnalysisJob.id,
                HardwareAnalysisJob.status,
                HardwareAnalysisJob.stage,
                HardwareAnalysisJob.progress,
                HardwareAnalysisJob.source_filename,
                HardwareAnalysisJob.plant_id,
                HardwareAnalysisJob.attempt_count,
                HardwareAnalysisJob.error_code,
                HardwareAnalysisJob.error_message,
                HardwareAnalysisJob.created_at,
                HardwareAnalysisJob.started_at,
                HardwareAnalysisJob.updated_at,
                HardwareAnalysisJob.completed_at,
            )
        )
        .order_by(
            HardwareAnalysisJob.created_at.desc(),
            HardwareAnalysisJob.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            serialize_hardware_analysis_job(job, include_result=False)
            for job in jobs
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@router.get("/jobs/{job_id}")
def get_hardware_analysis_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        normalized_job_id = str(UUID(job_id))
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tác vụ phân tích.",
        ) from error

    statement = select(HardwareAnalysisJob).where(
        HardwareAnalysisJob.id == normalized_job_id
    )
    statement = statement.options(
        load_only(
            HardwareAnalysisJob.id,
            HardwareAnalysisJob.owner_user_id,
            HardwareAnalysisJob.status,
            HardwareAnalysisJob.stage,
            HardwareAnalysisJob.progress,
            HardwareAnalysisJob.source_filename,
            HardwareAnalysisJob.plant_id,
            HardwareAnalysisJob.attempt_count,
            HardwareAnalysisJob.error_code,
            HardwareAnalysisJob.error_message,
            HardwareAnalysisJob.result_data,
            HardwareAnalysisJob.created_at,
            HardwareAnalysisJob.started_at,
            HardwareAnalysisJob.updated_at,
            HardwareAnalysisJob.completed_at,
        )
    )
    if user.role != "admin":
        statement = statement.where(
            HardwareAnalysisJob.owner_user_id == user.id
        )
    job = db.scalar(statement)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tác vụ phân tích.",
        )
    return serialize_hardware_analysis_job(job)


def process_hardware_analysis_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(HardwareAnalysisJob, job_id)
        if job is None or job.status != "running" or job.input_file is None:
            raise RuntimeError("Queued hardware analysis job is incomplete.")
        filename = job.source_filename
        content = bytes(job.input_file)
        custom_prompt = job.custom_prompt
        owner_user_id = job.owner_user_id
        plant_id = job.plant_id
        source_sha256 = job.source_file_sha256

    try:
        update_hardware_analysis_job_stage(
            job_id,
            stage="reading_file",
            progress=10,
        )
        dataframe = _parse_hardware_content(
            filename,
            content,
            allow_documents=True,
        )
        update_hardware_analysis_job_stage(
            job_id,
            stage="calculating_kpis",
            progress=35,
        )
        response = analyze_hardware_dataframe(dataframe)
        if custom_prompt:
            update_hardware_analysis_job_stage(
                job_id,
                stage="generating_ai_explanation",
                progress=75,
            )
            try:
                response["custom_prompt_response"] = (
                    generate_custom_hardware_response(
                        dict(response),
                        custom_prompt,
                    )
                )
            except Exception:
                logger.exception(
                    "Hardware custom prompt response failed: job_id=%s",
                    job_id,
                )
                response["custom_prompt_error"] = (
                    "Không thể tạo phản hồi theo prompt riêng. "
                    "Kết quả phân tích chính vẫn được giữ nguyên."
                )

        with SessionLocal() as db:
            job = db.scalar(
                select(HardwareAnalysisJob)
                .where(HardwareAnalysisJob.id == job_id)
                .with_for_update()
            )
            if job is None or job.status != "running":
                return

            encoded_result = jsonable_encoder(response)
            summary_value = encoded_result.get("summary")
            history = AnalysisHistory(
                analysis_type="hardware_analysis",
                title="Phân tích dữ liệu phần cứng",
                summary=summary_value if isinstance(summary_value, str) else None,
                source_filename=filename,
                result_data=encoded_result,
                creator_user_id=owner_user_id,
                plant_id=plant_id,
                source_file_sha256=source_sha256,
                algorithm_version="hardware-analysis-v1",
                ai_status=encoded_result.get("ai_status"),
                evidence=encoded_result.get("evidence"),
            )
            db.add(history)
            db.flush()
            response["analysis_history_id"] = history.id

            job.status = "succeeded"
            job.stage = "completed"
            job.progress = 100
            job.result_data = jsonable_encoder(response)
            job.input_file = None
            job.custom_prompt = ""
            job.error_code = None
            job.error_message = None
            job.lease_expires_at = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as error:
        logger.error(
            "Hardware analysis job failed: job_id=%s error_type=%s",
            job_id,
            type(error).__name__,
        )
        fail_hardware_analysis_job(job_id, error)


@router.post(
    "/compare",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "hardware-compare",
                limit=30,
                window_seconds=60,
            )
        )
    ],
)
async def compare_hardware(
    file: UploadFile = File(...),
    model_a: str | None = Form(default=None),
    model_b: str | None = Form(default=None),
    model_names: list[str] | None = Form(default=None),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    try:
        if model_names is not None:
            if model_a is not None or model_b is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Gửi danh sách model_names hoặc cặp model_a/model_b, không gửi đồng thời.",
                )
            selected_models = model_names
        elif model_a is not None and model_b is not None:
            selected_models = [model_a, model_b]
        else:
            raise HTTPException(
                status_code=422,
                detail="Vui lòng chọn ít nhất hai model để so sánh.",
            )

        dataframe = await _read_hardware_upload(file)
        try:
            comparison = await run_in_threadpool(
                compare_hardware_models,
                dataframe,
                selected_models,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

        try:
            comparison["ai_analysis"] = await run_in_threadpool(
                analyze_hardware_comparison,
                comparison,
            )
            comparison["ai_status"] = "available"
        except Exception:
            logger.exception("Hardware comparison AI analysis failed")
            comparison["ai_analysis"] = None
            comparison["ai_status"] = "unavailable"
            comparison["ai_error"] = AI_UNAVAILABLE_MESSAGE

        _save_hardware_history_safely(
            db,
            creator_user_id=user.id,
            source_file_sha256=_upload_sha256(file),
            analysis_type="hardware_comparison",
            title=f"So sánh {len(selected_models)} model phần cứng",
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


@router.post(
    "/compare-periods",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "hardware-compare-periods",
                limit=30,
                window_seconds=60,
            )
        )
    ],
)
async def compare_hardware_periods_endpoint(
    file: UploadFile = File(...),
    period_a_start: date = Form(...),
    period_a_end: date = Form(...),
    period_b_start: date = Form(...),
    period_b_end: date = Form(...),
    dimension: ComparisonDimension | None = Form(default=None),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
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
            creator_user_id=user.id,
            source_file_sha256=_upload_sha256(file),
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
    _: dict[str, Any] = Body(...),
):
    raise HTTPException(
        status_code=410,
        detail=(
            "Xuất báo cáo trực tiếp đã tắt. Hãy lưu báo cáo, gửi duyệt, "
            "phát hành rồi xuất từ Analysis Reports."
        ),
    )


@router.post(
    "/analyze-multiple",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "hardware-multi-file-analysis",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
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
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
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

    known_sizes = [file.size for file in files]
    if all(size is not None for size in known_sizes) and sum(
        size or 0 for size in known_sizes
    ) > MAX_MULTIPLE_TOTAL_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                "Tổng dung lượng tải lên không được vượt quá 25MB mỗi lần."
            ),
        )

    upload_results = []
    total_uploaded_bytes = 0
    total_rows = 0

    for file in files:
        filename = file.filename or "File không tên"
        try:
            remaining_size = MAX_MULTIPLE_TOTAL_SIZE - total_uploaded_bytes
            content = await _read_upload_content_with_limit(
                file,
                min(MAX_FILE_SIZE, remaining_size),
            )
            total_uploaded_bytes += len(content)
            dataframe = await run_in_threadpool(
                _parse_hardware_content,
                filename,
                content,
            )
        except HTTPException as error:
            if error.status_code == 413 and "Tổng dung lượng" in str(error.detail):
                raise
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
            total_rows += len(dataframe.index)
            if total_rows > MAX_MULTIPLE_TOTAL_ROWS:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Tổng số dòng vượt quá giới hạn "
                        f"{MAX_MULTIPLE_TOTAL_ROWS:,} dòng mỗi lần."
                    ),
                )
            upload_results.append(
                {
                    "filename": filename,
                    "dataframe": dataframe,
                }
            )

    result = normalize_hardware_files(upload_results)
    merged = merge_normalized_hardware_files(result)
    try:
        dataset_id = (
            store_merged_hardware_dataset(
                merged.dataframe,
                owner_user_id=user.id,
                db=db,
            )
            if merged.report["files_merged"] > 0
            else None
        )
    except DatasetCacheCapacityError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
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
            for row in merged.dataframe.head(
                MAX_MERGED_RESPONSE_ROWS
            ).to_dict(orient="records")
        ],
        "merged_rows_truncated": (
            len(merged.dataframe.index) > MAX_MERGED_RESPONSE_ROWS
        ),
        "files": normalized_files,
    }


@router.post("/trends")
async def analyze_hardware_trend(
    request: HardwareTrendRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    dataframe = get_merged_hardware_dataset(
        request.dataset_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        db=db,
    )
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
        creator_user_id=user.id,
        analysis_type="hardware_trends",
        title="Phân tích xu hướng phần cứng",
        dataset_id=request.dataset_id,
        result_data=result,
    )
    return result


@router.post("/evaluate-intervention")
def evaluate_hardware_intervention_endpoint(
    request: HardwareInterventionRequest,
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    dataframe = get_merged_hardware_dataset(
        request.dataset_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        db=db,
    )
    if dataframe is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Không tìm thấy dataset hoặc dataset đã hết hạn. "
                "Vui lòng gộp lại các file để nhận dataset_id mới."
            ),
        )

    try:
        result = evaluate_hardware_intervention(
            dataframe,
            before_start_date=request.before_start_date,
            before_end_date=request.before_end_date,
            after_start_date=request.after_start_date,
            after_end_date=request.after_end_date,
            model=request.model,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    try:
        result["ai_explanation"] = explain_hardware_intervention(result)
        result["ai_status"] = "available"
    except Exception:
        logger.exception("Hardware intervention AI explanation failed")
        result["ai_status"] = "unavailable"
        result["ai_error"] = AI_UNAVAILABLE_MESSAGE

    _save_hardware_history_safely(
        db,
        creator_user_id=user.id,
        analysis_type="hardware_intervention",
        title="Đánh giá hiệu quả can thiệp sản xuất",
        dataset_id=request.dataset_id,
        result_data=result,
    )
    return result
