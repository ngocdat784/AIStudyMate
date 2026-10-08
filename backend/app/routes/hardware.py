from fastapi import APIRouter, File, HTTPException, UploadFile
import io
import pandas as pd

from app.services.hardware_service import (
    analyze_hardware_dataframe,
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
            last_error = None

            encodings = [
                "utf-8-sig",
                "utf-8",
                "cp1258",
                "latin-1",
            ]

            for encoding in encodings:
                try:
                    text = content.decode(
                        encoding,
                        errors="strict",
                    )

                    lines = [
                        line.strip()
                        for line in text.splitlines()
                        if line.strip()
                    ]

                    if not lines:
                        raise ValueError(
                            "File CSV không có dữ liệu."
                        )

                    # ==========================================
                    # Detect delimiter
                    # ==========================================

                    raw_header = lines[0]

                    comma_count = raw_header.count(",")
                    semicolon_count = raw_header.count(";")

                    if semicolon_count > comma_count:
                        delimiter = ";"
                    else:
                        delimiter = ","

                    print("========================================")
                    print("CSV ENCODING:", encoding)
                    print("CSV DELIMITER:", repr(delimiter))
                    print("CSV HEADER:", raw_header)
                    print("========================================")

                    # ==========================================
                    # Clean header
                    # ==========================================

                    header = raw_header.strip()

                    if (
                        header.startswith('"')
                        and header.endswith('"')
                    ):
                        header = header[1:-1]

                    columns = [
                        column.strip().strip('"').strip("'")
                        for column in header.split(delimiter)
                    ]

                    print("DETECTED COLUMNS:")
                    print(columns)

                    # ==========================================
                    # Parse rows manually
                    # ==========================================

                    rows = []

                    for line_number, line in enumerate(
                        lines[1:],
                        start=2,
                    ):
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

                        if len(values) != len(columns):
                            print(
                                f"CSV WARNING: line {line_number} "
                                f"has {len(values)} columns, "
                                f"expected {len(columns)}"
                            )

                            if len(values) < len(columns):
                                values.extend(
                                    [None] * (len(columns) - len(values))
                                )
                            else:
                                values = values[:len(columns)]

                        rows.append(values)

                    # ==========================================
                    # Create DataFrame
                    # ==========================================

                    dataframe = pd.DataFrame(
                        rows,
                        columns=columns,
                    )

                    # ==========================================
                    # Clean column names
                    # ==========================================

                    dataframe.columns = (
                        dataframe.columns
                        .astype(str)
                        .str.strip()
                    )

                    # ==========================================
                    # Debug
                    # ==========================================

                    print("CSV COLUMNS:")
                    print(dataframe.columns.tolist())
                    print("CSV ROWS:", len(dataframe))
                    print("CSV PREVIEW:")
                    print(dataframe.head().to_string())
                    if not dataframe.empty:
                        print("CSV FIRST ROW:")
                        print(dataframe.iloc[0].to_dict())

                    print("========================================")

                    break

                except Exception as error:
                    last_error = error
                    dataframe = None

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

        return analyze_hardware_dataframe(dataframe)

    except HTTPException:
        raise

    except Exception as error:

        import traceback

        print("========================================")
        print("HARDWARE ANALYSIS ERROR")
        print("========================================")
        print("Error:", repr(error))
        traceback.print_exc()
        print("========================================")

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )