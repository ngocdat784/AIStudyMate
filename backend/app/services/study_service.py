from google import genai
from pydantic import BaseModel, Field

from app.config import settings


GEMINI_MODEL = "gemini-3.5-flash-lite"


class Flashcard(BaseModel):
    question: str
    answer: str


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    correct_answer: str
    explanation: str


class StudyMaterial(BaseModel):
    title: str

    summary: str

    key_points: list[str] = Field(
        min_length=3,
        max_length=8,
    )

    flashcards: list[Flashcard] = Field(
        min_length=4,
        max_length=10,
    )

    quiz: list[QuizQuestion] = Field(
        min_length=5,
        max_length=10,
    )

    study_plan: list[str] = Field(
        min_length=3,
        max_length=7,
    )


client = genai.Client(
    api_key=settings.GEMINI_API_KEY
)


SYSTEM_INSTRUCTION = """
You are StudyMate, an expert AI learning assistant.

Your job is to transform educational material into
useful study material for students.

The input may be Vietnamese or English.

You must return structured JSON containing:

1. title
2. summary
3. key_points
4. flashcards
5. quiz
6. study_plan

Rules:

- Preserve the factual meaning of the source material.
- Do not invent information that is not supported by the material.
- Use clear and natural Vietnamese when the source is Vietnamese.
- Make the summary concise but informative.
- Extract the most important concepts as key points.
- Create useful flashcards that test understanding.
- Create multiple-choice questions based only on the material.
- Every quiz question must have exactly 4 options.
- correct_answer must exactly match one of the options.
- Explanation must explain why the answer is correct.
- Create a practical study plan based on the material.
- Avoid repeating the exact same question.
- Make the questions useful for actual studying.
- Do not include markdown.
- Return only structured JSON.
"""


def generate_study_material(
    content: str,
) -> StudyMaterial:

    user_input = f"""
Here is the study material:

--------------------
{content}
--------------------

Create a complete study set from this material.
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=SYSTEM_INSTRUCTION + "\n" + user_input,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": StudyMaterial.model_json_schema(),
        },
    )

    if not interaction.output_text:
        raise Exception(
            "Gemini không trả về nội dung học tập."
        )

    try:
        return StudyMaterial.model_validate_json(
            interaction.output_text
        )

    except Exception as e:
        raise Exception(
            f"Gemini trả về dữ liệu không hợp lệ: {str(e)}"
        )