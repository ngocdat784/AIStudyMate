from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.document_parser import extract_text
from app.services.study_service import generate_study_material


router = APIRouter(
    prefix="/api/study",
    tags=["StudyMate"],
)


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
}


MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/generate")
async def generate_study(
    content: str = Form(""),
    file: UploadFile | None = File(None),
):
    try:
        final_content = content.strip()

        # -----------------------------
        # Nếu user upload file
        # -----------------------------

        if file is not None:

            if not file.filename:
                raise HTTPException(
                    status_code=400,
                    detail="Tên file không hợp lệ.",
                )

            filename = file.filename.lower()

            extension = ""

            if "." in filename:
                extension = "." + filename.rsplit(".", 1)[1]

            if extension not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Chỉ hỗ trợ file PDF, DOCX hoặc TXT."
                    ),
                )

            file_bytes = await file.read()

            if not file_bytes:
                raise HTTPException(
                    status_code=400,
                    detail="File rỗng.",
                )

            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="File không được vượt quá 10MB.",
                )

            try:
                final_content = extract_text(
                    file_bytes=file_bytes,
                    filename=file.filename,
                    content_type=file.content_type,
                )

            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

        # -----------------------------
        # Không có nội dung
        # -----------------------------

        if not final_content:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Vui lòng nhập nội dung "
                    "hoặc upload tài liệu."
                ),
            )

        # -----------------------------
        # Generate study material
        # -----------------------------

        result = generate_study_material(
            content=final_content,
        )

        return result.model_dump()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"StudyMate failed: {str(e)}",
        )