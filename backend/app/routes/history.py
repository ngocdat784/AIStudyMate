from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models import AnalysisHistory
from app.services.analysis_history_service import (
    delete_analysis_history,
    get_analysis_history,
    list_analysis_history,
)


router = APIRouter(
    prefix="/api/history",
    tags=["HardwareAI - Analysis History"],
)


def _serialize_history(
    history: AnalysisHistory,
    *,
    include_result: bool = False,
) -> dict[str, Any]:
    created_at: datetime | None = history.created_at
    data: dict[str, Any] = {
        "id": history.id,
        "analysis_type": history.analysis_type,
        "title": history.title,
        "summary": history.summary,
        "source_filename": history.source_filename,
        "dataset_id": history.dataset_id,
        "created_at": (
            created_at.isoformat() if created_at is not None else None
        ),
    }

    if include_result:
        data["result_data"] = history.result_data
    return data


@router.get("")
def get_history_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    analysis_type: str | None = Query(default=None, min_length=1),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = list_analysis_history(
        db,
        page=page,
        page_size=page_size,
        analysis_type=analysis_type,
    )
    return {
        "items": [
            _serialize_history(item)
            for item in result["items"]
        ],
        "pagination": {
            "page": result["page"],
            "page_size": result["page_size"],
            "total": result["total"],
            "total_pages": result["total_pages"],
        },
    }


@router.get("/{history_id}")
def get_history_detail(
    history_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    history = get_analysis_history(db, history_id)
    if history is None:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy lịch sử phân tích.",
        )
    return _serialize_history(history, include_result=True)


@router.delete("/{history_id}")
def delete_history(
    history_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    deleted = delete_analysis_history(db, history_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy lịch sử phân tích.",
        )
    return {
        "message": "Đã xóa lịch sử phân tích.",
        "id": history_id,
    }
