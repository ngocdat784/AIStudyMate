import hashlib
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.routes.hardware import _parse_hardware_content, _read_upload_content
from app.services.analysis_history_service import create_analysis_history
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import get_current_user, require_roles
from app.services.production_service import (
    analyze_production_dataframe,
    calculate_production_kpis,
)

router = APIRouter(
    prefix="/api/production",
    tags=["Production Analysis"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)
logger = logging.getLogger(__name__)


def _analyze_production_dataframe(dataframe):
    kpis = calculate_production_kpis(dataframe)
    analysis = analyze_production_dataframe(dataframe)
    response = analysis.model_dump()
    response["machine_performance"] = kpis.get("machine_performance", [])
    response["daily_production"] = kpis.get("daily_production", [])
    response["data_scope"] = kpis.get("data_scope", {})
    return response


@router.post(
    "/analyze",
    dependencies=[
        Depends(
            rate_limit_dependency(
                "production-analysis",
                limit=10,
                window_seconds=60,
            )
        )
    ],
)
async def analyze_production(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
):
    content = await _read_upload_content(file)
    try:
        dataframe = await run_in_threadpool(
            _parse_hardware_content,
            file.filename or "",
            content,
        )
        response = await run_in_threadpool(
            _analyze_production_dataframe,
            dataframe,
        )

        try:
            create_analysis_history(
                db,
                analysis_type="production_analysis",
                title="Phân tích dữ liệu sản xuất",
                summary=(
                    response.get("summary")
                    if isinstance(response.get("summary"), str)
                    else None
                ),
                source_filename=file.filename,
                result_data=response,
                creator_user_id=user.id,
                source_file_sha256=hashlib.sha256(content).hexdigest(),
                algorithm_version="production-kpi-v1",
                ai_status=response.get("ai_status"),
                evidence=response.get("evidence"),
            )
        except Exception:
            db.rollback()
            logger.exception("Could not save production analysis lineage.")
        return response
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Production analysis failed")
        raise HTTPException(
            status_code=500,
            detail="Không thể phân tích dữ liệu sản xuất.",
        ) from error
