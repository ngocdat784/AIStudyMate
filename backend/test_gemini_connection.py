from google import genai
from google.genai import types

from app.config import settings

MODEL = "gemini-3.5-flash-lite"

client = genai.Client(api_key=settings.GEMINI_API_KEY)

try:
    response = client.models.generate_content(
        model=MODEL,
        contents="Reply with exactly: OK",
        config=types.GenerateContentConfig(
            automatic_function_calling=(
                types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            )
        ),
    )

    print("SUCCESS")
    print(response.text)

except Exception as exc:
    print("FAILED")
    print("Exception type:", type(exc).__name__)
    print("Error:", str(exc))

    if hasattr(exc, "details"):
        print("Details:", exc.details)