import hashlib
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db
from app.models.monitoring import AlertUser
from app.routes.hardware import _parse_hardware_content, _read_upload_content
from app.services.analysis_history_service import create_analysis_history
from app.services.api_rate_limit import rate_limit_dependency
from app.services.monitoring_auth import get_current_user, require_roles
from app.services.production_intelligence_service import (
    analyze_production_intelligence,
)

router = APIRouter(
    prefix="/api/production",
    tags=["Production Analysis"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)
logger = logging.getLogger(__name__)


def _analyze_production_dataframe(
    dataframe,
    *,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    include_ai: bool = True,
):
    production_intelligence = analyze_production_intelligence(
        dataframe,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        cycle_time_unit=cycle_time_unit,
        duration_unit=duration_unit,
        production_unit=production_unit,
        include_ai=include_ai,
    )
    ai_interpretation = production_intelligence["ai_interpretation"]
    summary = production_intelligence["executive_summary"]
    labels = {
        "planned_production": "Planned Production",
        "actual_production": "Actual Production",
        "production_achievement_rate": "Production Achievement Rate",
        "plan_variance": "Production Plan Variance",
        "production_rate": "Production Rate",
        "throughput": "Throughput",
        "cycle_time": "Cycle Time",
        "takt_time": "Takt Time",
        "line_efficiency": "Line Efficiency",
        "line_utilization": "Line Utilization",
        "waiting_time": "Waiting Time",
    }
    kpis = [
        {
            "name": label,
            "value": metric["value"],
            "unit": metric["unit"],
            "description": metric["basis"],
        }
        for key, label in labels.items()
        if (metric := production_intelligence["kpi_results"][key])[
            "value"
        ]
        is not None
    ]
    if ai_interpretation:
        insights = [
            answer["text"]
            for answer in ai_interpretation["answers"]
        ]
        recommendations = [
            item["text"] for item in ai_interpretation["recommendations"]
        ]
    else:
        insights = [
            item["answer"]
            for item in production_intelligence["management_answers"].values()
            if item is not None
        ]
        recommendations = [
            item["action"]
            for item in production_intelligence["recommendations"]
        ]
    return {
        "title": "Production Intelligence",
        "summary": summary,
        "kpis": kpis,
        "insights": insights or [summary],
        "warnings": production_intelligence["analysis_limitations"][:10],
        "recommendations": recommendations or [
            "Bổ sung dữ liệu vận hành để tạo đề xuất có bằng chứng."
        ],
        "ai_status": production_intelligence["ai_status"],
        "ai_error": production_intelligence["ai_error"],
        "evidence": production_intelligence["evidence_references"],
        "production_intelligence": production_intelligence,
        "machine_performance": production_intelligence["machine_performance"],
        "daily_production": production_intelligence["daily_production"],
        "data_scope": production_intelligence["data_scope"],
        "data_quality": production_intelligence["data_quality"],
    }


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
    source_timezone: str = Form(default="UTC", max_length=80),
    downtime_unit: str = Form(default="minutes"),
    cycle_time_unit: str = Form(default="seconds"),
    duration_unit: str = Form(default="minutes"),
    production_unit: str = Form(default="unit", max_length=40),
    include_ai: bool = Form(default=True),
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
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            cycle_time_unit=cycle_time_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            include_ai=include_ai,
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
                algorithm_version="production-intelligence-v2.1",
                ai_status=response.get("ai_status"),
                evidence=response.get("evidence"),
            )
        except Exception:
            db.rollback()
            logger.exception("Could not save production analysis lineage.")
        return response
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error
    except Exception as error:
        logger.exception("Production analysis failed")
        raise HTTPException(
            status_code=500,
            detail="Không thể phân tích dữ liệu sản xuất.",
        ) from error
