
from google import genai
from app.config import settings

client = genai.Client(
    api_key=settings.GEMINI_API_KEY
)

MODEL_NAME = "gemini-3.5-flash-lite"

try:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents="Reply with exactly: GEMINI_OK",
    )

    print("SUCCESS")
    print("Response:", response.text)

except Exception as exc:
    print("FAILED")
    print("Error type:", type(exc).__name__)
    print("Error:", str(exc))

