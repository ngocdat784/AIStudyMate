from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
import hashlib
import io
import logging
import pandas as pd
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.services.analysis_history_service import create_analysis_history
from app.services.monitoring_auth import get_current_user
from app.services.production_service import (
    analyze_production_dataframe,
    calculate_production_kpis,
)
from app.services.monitoring_auth import require_roles

router = APIRouter(
    prefix="/api/production",
    tags=["Production Analysis"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)
logger = logging.getLogger(__name__)


MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/analyze")
async def analyze_production(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Vui lòng chọn file dữ liệu."
        )

    extension = (
        "." + file.filename.split(".")[-1].lower()
    )

    if extension not in [".csv", ".xlsx"]:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ file CSV hoặc XLSX."
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail="File không được vượt quá 10MB."
        )

    try:
        # -----------------------------
        # Read CSV
        # -----------------------------

        if extension == ".csv":

            dataframe = None

            encodings = [
                "utf-8-sig",
                "utf-8",
                "cp1258",
                "latin-1",
            ]

            last_error = None

            for encoding in encodings:
                try:
                    dataframe = pd.read_csv(
                        io.BytesIO(content),
                        encoding=encoding,
                    )
                    break
                except Exception as error:
                    last_error = error

            if dataframe is None:
                raise last_error or ValueError(
                    "Không thể đọc file CSV."
                )

        # -----------------------------
        # Read XLSX
        # -----------------------------

        else:
            excel = pd.ExcelFile(
                io.BytesIO(content),
                engine="openpyxl",
            )

            frames = []

            for sheet_name in excel.sheet_names:

                sheet_df = pd.read_excel(
                    excel,
                    sheet_name=sheet_name,
                    engine="openpyxl",
                )

                if sheet_df.empty:
                    continue

                sheet_df["_source_sheet"] = sheet_name

                frames.append(sheet_df)

            if not frames:
                raise ValueError(
                    "File Excel không chứa dữ liệu."
                )

            dataframe = pd.concat(
                frames,
                ignore_index=True,
            )

        # -----------------------------
        # Validate
        # -----------------------------

        if dataframe.empty:
            raise HTTPException(
                status_code=400,
                detail="File không chứa dữ liệu."
            )

        # -----------------------------
        # Python calculates KPI
        # -----------------------------

        kpis = calculate_production_kpis(
            dataframe
        )

        # -----------------------------
        # Gemini interprets KPI
        # -----------------------------

        analysis = analyze_production_dataframe(
            dataframe
        )

        # -----------------------------
        # Return dashboard data
        # -----------------------------

        response = analysis.model_dump()

        response["machine_performance"] = (
            kpis.get(
                "machine_performance",
                []
            )
        )

        response["daily_production"] = (
            kpis.get(
                "daily_production",
                []
            )
        )

        response["data_scope"] = (
            kpis.get(
                "data_scope",
                {}
            )
        )

        try:
            create_analysis_history(
                db,
                analysis_type="production_analysis",
                title="Phân tích dữ liệu sản xuất",
                summary=(
                    response.get("summary")
                    if isinstance(response.get("summary"), str)
                    else None
                ),
                source_filename=file.filename,
                result_data=response,
                creator_user_id=user.id,
                source_file_sha256=hashlib.sha256(content).hexdigest(),
                algorithm_version="production-kpi-v1",
                ai_status=response.get("ai_status"),
                evidence=response.get("evidence"),
            )
        except Exception:
            logger.exception("Could not save production analysis lineage.")
        return response

    except HTTPException:
        raise

    except Exception as error:
        print(
            "Production analysis error:",
            repr(error),
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Không thể phân tích dữ liệu sản xuất."
            ),
        )