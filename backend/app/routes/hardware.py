import io
import logging

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.hardware_ai_service import analyze_hardware_comparison
from app.services.hardware_service import (
    analyze_hardware_dataframe,
    compare_hardware_models,
)


router = APIRouter(
    prefix="/api/hardware",
    tags=["HardwareAI - Hardware Analysis"],
)

logger = logging.getLogger(__name__)
MAX_FILE_SIZE = 10 * 1024 * 1024
AI_UNAVAILABLE_MESSAGE = (
    "Dịch vụ AI hiện tạm thời không khả dụng. "
    "Các chỉ số so sánh được tính bằng Python vẫn có thể sử dụng."
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

    if extension == ".csv":
        dataframe = None
        last_error = None

        for encoding in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
            try:
                text = content.decode(encoding, errors="strict")
                lines = [
                    line.strip()
                    for line in text.splitlines()
                    if line.strip()
                ]
                if not lines:
                    raise ValueError("File CSV không có dữ liệu.")

                raw_header = lines[0]
                delimiter = (
                    ";"
                    if raw_header.count(";") > raw_header.count(",")
                    else ","
                )
                header = raw_header.strip()
                if header.startswith('"') and header.endswith('"'):
                    header = header[1:-1]
                columns = [
                    column.strip().strip('"').strip("'")
                    for column in header.split(delimiter)
                ]

                rows = []
                for line in lines[1:]:
                    cleaned_line = line.strip()
                    if (
                        cleaned_line.startswith('"')
                        and cleaned_line.endswith('"')
                    ):
                        cleaned_line = cleaned_line[1:-1]
                    values = [
                        value.strip().strip('"').strip("'")
                        for value in cleaned_line.split(delimiter)
                    ]
                    if len(values) < len(columns):
                        values.extend([None] * (len(columns) - len(values)))
                    else:
                        values = values[:len(columns)]
                    rows.append(values)

                dataframe = pd.DataFrame(rows, columns=columns)
                dataframe.columns = (
                    dataframe.columns.astype(str).str.strip()
                )
                break
            except Exception as error:
                last_error = error
                dataframe = None

        if dataframe is None:
            raise last_error or ValueError("Không thể đọc file CSV.")
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


@router.post("/analyze")
async def analyze_hardware(
    file: UploadFile = File(...),
):
    try:
        dataframe = await _read_hardware_upload(file)
        response = analyze_hardware_dataframe(dataframe)
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

        return comparison
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Hardware comparison failed")
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error
