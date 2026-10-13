from google import genai
from pydantic import BaseModel
from app.config import settings


MODEL_NAME = "gemma-4-26b-a4b-it"


class TestResult(BaseModel):
    title: str
    summary: str
    risk_level: str


client = genai.Client(api_key=settings.GEMINI_API_KEY)

try:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=(
            "Tạo báo cáo kiểm tra thử cho một nhà máy phần cứng. "
            "Tiêu đề là Kết quả kiểm tra. "
            "Tóm tắt là Đã kiểm tra 100 sản phẩm và phát hiện 3 sản phẩm lỗi. "
            "Mức độ rủi ro là thấp."
        ),
        config={
            "response_mime_type": "application/json",
            "response_schema": TestResult,
        },
    )

    print("SUCCESS")
    print("Response:", response.text)

    result = TestResult.model_validate_json(response.text)
    print("JSON hợp lệ:", result.model_dump())

except Exception as exc:
    print("FAILED")
    print("Error type:", type(exc).__name__)
    print("Error:", str(exc))