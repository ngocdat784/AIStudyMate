from typing import List

from google import genai
from pydantic import BaseModel, Field

from app.config import settings


GEMINI_MODEL = "gemini-3.5-flash-lite"


class Flashcard(BaseModel):
    question: str = Field(
        description="A question that tests an important concept from the study material."
    )
    answer: str = Field(
        description="A concise and accurate answer to the question."
    )


class QuizQuestion(BaseModel):
    question: str = Field(
        description="A multiple-choice question based only on the study material."
    )
    options: List[str] = Field(
        description="Exactly four answer options."
    )
    correct_answer: str = Field(
        description="The correct answer, matching exactly one of the options."
    )
    explanation: str = Field(
        description="A short explanation of why the answer is correct."
    )


class StudyResult(BaseModel):
    title: str = Field(
        description="A suitable title for the study material."
    )

    summary: str = Field(
        description="A clear summary of the study material in Vietnamese."
    )

    key_points: List[str] = Field(
        description="The most important points the student should remember."
    )

    flashcards: List[Flashcard] = Field(
        description="Useful flashcards generated from the study material."
    )

    quiz: List[QuizQuestion] = Field(
        description="Multiple-choice questions for self-testing."
    )

    study_plan: List[str] = Field(
        description="A short step-by-step study plan for this material."
    )


client = genai.Client(
    api_key=settings.GEMINI_API_KEY
)


SYSTEM_INSTRUCTION = """
You are an AI study assistant called StudyMate.

Your job is to transform study material into useful learning content.

The user may provide:
- lecture notes
- textbook content
- technical documentation
- programming notes
- exam preparation material
- copied text from a document

Your tasks:

1. Understand the material accurately.
2. Create a concise summary in Vietnamese.
3. Extract the most important concepts.
4. Create useful flashcards.
5. Create multiple-choice quiz questions.
6. Create a practical study plan.

Rules:

- Use only information contained in the provided study material.
- Do not invent facts that are not supported by the material.
- Keep explanations clear and suitable for students.
- Use Vietnamese unless the source material clearly requires another language.
- Flashcards should test important concepts rather than trivial details.
- Quiz questions must have exactly four options.
- The correct answer must exactly match one of the four options.
- Do not make every question extremely easy.
- The study plan should progress from understanding to memorization and practice.
- Return only structured JSON matching the requested schema.
"""


def generate_study_material(
    content: str,
) -> StudyResult:

    if not content.strip():
        raise ValueError("Study material cannot be empty.")

    user_input = f"""
Study material:

{content}
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=SYSTEM_INSTRUCTION + "\n" + user_input,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": StudyResult.model_json_schema(),
        },
    )

    if not interaction.output_text:
        raise Exception(
            "Gemini did not return study material."
        )

    try:
        return StudyResult.model_validate_json(
            interaction.output_text
        )
    except Exception as e:
        raise Exception(
            f"Invalid Gemini response: {str(e)}"
        )