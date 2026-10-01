from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


MAX_TEXT_LENGTH = 50000


def extract_text_from_txt(file_bytes: bytes) -> str:
    encodings = [
        "utf-8",
        "utf-8-sig",
        "cp1258",
        "latin-1",
    ]

    for encoding in encodings:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError("Không thể đọc file TXT.")


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(file_bytes))

    pages = []

    for page in reader.pages:
        text = page.extract_text()

        if text:
            pages.append(text)

    return "\n\n".join(pages)


def extract_text_from_docx(file_bytes: bytes) -> str:
    document = Document(BytesIO(file_bytes))

    paragraphs = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()

        if text:
            paragraphs.append(text)

    return "\n".join(paragraphs)


def extract_text(
    file_bytes: bytes,
    filename: str,
    content_type: str | None = None,
) -> str:

    extension = Path(filename).suffix.lower()

    if extension == ".txt" or content_type == "text/plain":
        text = extract_text_from_txt(file_bytes)

    elif extension == ".pdf" or content_type == "application/pdf":
        text = extract_text_from_pdf(file_bytes)

    elif (
        extension == ".docx"
        or content_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        text = extract_text_from_docx(file_bytes)

    else:
        raise ValueError(
            "Định dạng file không được hỗ trợ. "
            "Chỉ hỗ trợ PDF, DOCX và TXT."
        )

    text = text.strip()

    if not text:
        raise ValueError(
            "Không tìm thấy nội dung văn bản trong file."
        )

    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]

    return text