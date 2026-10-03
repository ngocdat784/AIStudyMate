from fastapi import APIRouter, File, HTTPException, UploadFile
import io
import pandas as pd

from app.services.production_service import (
    analyze_production_dataframe,
    calculate_production_kpis,
)

router = APIRouter(
    prefix="/api/production",
    tags=["TextileAI - Production"],
)


MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/analyze")
async def analyze_production(
    file: UploadFile = File(...)
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