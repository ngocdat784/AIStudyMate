from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.study_service import generate_study_material


router = APIRouter(
    prefix="/api/study",
    tags=["Study"],
)


class StudyRequest(BaseModel):
    content: str = Field(
        min_length=10,
        max_length=50000,
        description="Study material provided by the user.",
    )


@router.post("/generate")
async def generate_study(request: StudyRequest):

    if not request.content.strip():
        raise HTTPException(
            status_code=400,
            detail="Nội dung học tập không được để trống.",
        )

    try:
        result = generate_study_material(
            content=request.content
        )

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Study generation failed: {str(e)}",
        )