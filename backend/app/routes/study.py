from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.document_parser import extract_text
from app.services.study_service import generate_study_material


router = APIRouter(
    prefix="/api/study",
    tags=["TextileAI - Training"],
)


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
}

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_CONTENT_LENGTH = 100_000


@router.post("/generate")
async def generate_study(
    content: str = Form(""),
    file: UploadFile | None = File(None),
):
    """
    TextileAI - Training & Document Analysis

    Nhận:
    - Nội dung văn bản trực tiếp
    - Hoặc tài liệu PDF/DOCX/TXT

    Trả về:
    - Summary
    - Key Points
    - Flashcards
    - Quiz
    - Study Plan
    """

    try:
        final_content = content.strip()

        # =========================================================
        # 1. Kiểm tra nội dung nhập trực tiếp
        # =========================================================

        if len(final_content) > MAX_CONTENT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nội dung quá dài. "
                    "Vui lòng giới hạn nội dung trong 100.000 ký tự."
                ),
            )

        # =========================================================
        # 2. Nếu người dùng upload tài liệu
        # =========================================================

        if file is not None:

            if not file.filename:
                raise HTTPException(
                    status_code=400,
                    detail="Tên file không hợp lệ.",
                )

            filename = file.filename.strip()

            if not filename:
                raise HTTPException(
                    status_code=400,
                    detail="Tên file không hợp lệ.",
                )

            # Lấy extension
            extension = ""

            if "." in filename:
                extension = "." + filename.rsplit(".", 1)[1].lower()

            # Kiểm tra định dạng
            if extension not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "TextileAI hiện hỗ trợ "
                        "PDF, DOCX và TXT."
                    ),
                )

            # Đọc file
            file_bytes = await file.read()

            if not file_bytes:
                raise HTTPException(
                    status_code=400,
                    detail="File rỗng.",
                )

            # Kiểm tra kích thước
            if len(file_bytes) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="File không được vượt quá 10MB.",
                )

            # =====================================================
            # Trích xuất nội dung tài liệu
            # =====================================================

            try:
                extracted_text = extract_text(
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=file.content_type,
                )

            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

            # File upload được ưu tiên hơn nội dung textarea
            final_content = extracted_text.strip()

        # =========================================================
        # 3. Kiểm tra nội dung sau khi xử lý
        # =========================================================

        if not final_content:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Vui lòng nhập nội dung "
                    "hoặc upload tài liệu."
                ),
            )

        if len(final_content) > MAX_CONTENT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nội dung tài liệu quá dài. "
                    "Vui lòng sử dụng tài liệu ngắn hơn."
                ),
            )

        # =========================================================
        # 4. AI phân tích tài liệu
        # =========================================================

        result = generate_study_material(
            content=final_content,
        )

        # =========================================================
        # 5. Trả kết quả
        # =========================================================

        return result.model_dump()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"TextileAI failed: {str(e)}",
        )