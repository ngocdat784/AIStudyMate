from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalysisHistory


def create_analysis_history(
    db: Session,
    *,
    analysis_type: str,
    result_data: dict[str, Any],
    title: str | None = None,
    summary: str | None = None,
    source_filename: str | None = None,
    dataset_id: str | None = None,
) -> AnalysisHistory:
    """Save one analysis result to the database."""
    history = AnalysisHistory(
        analysis_type=analysis_type,
        title=title,
        summary=summary,
        source_filename=source_filename,
        dataset_id=dataset_id,
        result_data=result_data,
    )

    try:
        db.add(history)
        db.commit()
        db.refresh(history)
        return history
    except Exception:
        db.rollback()
        raise


def list_analysis_history(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    analysis_type: str | None = None,
) -> dict[str, Any]:
    """Return paginated history, newest first."""
    filters = []
    if analysis_type:
        filters.append(AnalysisHistory.analysis_type == analysis_type)

    count_statement = select(func.count()).select_from(AnalysisHistory)
    data_statement = select(AnalysisHistory)
    if filters:
        count_statement = count_statement.where(*filters)
        data_statement = data_statement.where(*filters)

    total = db.scalar(count_statement) or 0
    data_statement = (
        data_statement
        .order_by(
            AnalysisHistory.created_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(db.scalars(data_statement).all())

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def get_analysis_history(
    db: Session,
    history_id: int,
) -> AnalysisHistory | None:
    """Return one history record by ID."""
    return db.get(AnalysisHistory, history_id)


def delete_analysis_history(
    db: Session,
    history_id: int,
) -> bool:
    """Delete one history record; return False if it does not exist."""
    history = db.get(AnalysisHistory, history_id)
    if history is None:
        return False

    try:
        db.delete(history)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
