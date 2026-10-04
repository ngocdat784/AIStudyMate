from fastapi import APIRouter, File, HTTPException, UploadFile
import io
import pandas as pd

from app.services.hardware_service import (
    analyze_hardware_dataframe,
    calculate_hardware_kpis,
)


router = APIRouter(
    prefix="/api/hardware",
    tags=["HardwareAI - Hardware Analysis"],
)


MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/analyze")
async def analyze_hardware(
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
        # =================================================
        # Read CSV
        # =================================================

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

        # =================================================
        # Read XLSX
        # =================================================

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

        # =================================================
        # Validate
        # =================================================

        if dataframe.empty:
            raise HTTPException(
                status_code=400,
                detail="File không chứa dữ liệu."
            )

        # =================================================
        # Calculate Hardware KPIs
        # =================================================

        kpis = calculate_hardware_kpis(
            dataframe
        )

        # =================================================
        # Gemini Hardware Analysis
        # =================================================

        analysis = analyze_hardware_dataframe(
            dataframe
        )

        # =================================================
        # Return Dashboard Data
        # =================================================

        response = analysis.model_dump()

        # -------------------------------------------------
        # Product
        # -------------------------------------------------

        response["product_performance"] = (
            kpis.get(
                "product_performance",
                []
            )
        )

        # -------------------------------------------------
        # Model
        # -------------------------------------------------

        response["model_performance"] = (
            kpis.get(
                "model_performance",
                []
            )
        )

        # -------------------------------------------------
        # Component
        # -------------------------------------------------

        response["component_performance"] = (
            kpis.get(
                "component_performance",
                []
            )
        )

        # -------------------------------------------------
        # Supplier
        # -------------------------------------------------

        response["supplier_performance"] = (
            kpis.get(
                "supplier_performance",
                []
            )
        )

        # -------------------------------------------------
        # Batch
        # -------------------------------------------------

        response["batch_performance"] = (
            kpis.get(
                "batch_performance",
                []
            )
        )

        # -------------------------------------------------
        # Defect Type
        # -------------------------------------------------

        response["defect_type_analysis"] = (
            kpis.get(
                "defect_type_analysis",
                []
            )
        )

        # -------------------------------------------------
        # Daily Production
        # -------------------------------------------------

        response["daily_production"] = (
            kpis.get(
                "daily_production",
                []
            )
        )

        # -------------------------------------------------
        # Data Scope
        # -------------------------------------------------

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
            "Hardware analysis error:",
            repr(error),
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Không thể phân tích dữ liệu phần cứng."
            ),
        )