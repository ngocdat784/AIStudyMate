from contextlib import asynccontextmanager
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes.production import router as production_router
from app.routes.hardware import (
    process_hardware_analysis_job,
    router as hardware_router,
)
from app.routes.history import router as history_router
from app.routes.intelligence import router as intelligence_router
from app.routes.improvements import router as improvements_router
from app.routes.integrations import router as integrations_router
from app.routes.monitoring import router as monitoring_router
from app.routes.observability import router as observability_router
from app.routes.quality import router as quality_router
from app.routes.reports import router as reports_router
from app.routes.root_cause import router as root_cause_router
from app.config import settings, validate_security_settings
from app.services.monitoring_scheduler import start_scheduler, stop_scheduler
from app.services.hardware_analysis_jobs import (
    start_hardware_analysis_workers,
    stop_hardware_analysis_workers,
)
from app.services.observability import (
    configure_application_logging,
    request_id_context,
    request_metrics,
)


configure_application_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_security_settings()
    if settings.ALERT_SCHEDULER_ENABLED:
        start_scheduler()
    if settings.HARDWARE_ANALYSIS_JOBS_ENABLED:
        start_hardware_analysis_workers(process_hardware_analysis_job)
    try:
        yield
    finally:
        stop_hardware_analysis_workers()
        stop_scheduler()

app = FastAPI(
    title="AI Production and Hardware API",
    description="AI-powered production and hardware analysis",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(production_router)
app.include_router(hardware_router)
app.include_router(history_router)
app.include_router(intelligence_router)
app.include_router(quality_router)
app.include_router(root_cause_router)
app.include_router(improvements_router)
app.include_router(integrations_router)
app.include_router(monitoring_router)
app.include_router(reports_router)
app.include_router(observability_router)


@app.middleware("http")
async def observe_http_request(request: Request, call_next):
    request_id = str(uuid4())
    context_token = request_id_context.set(request_id)
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        route = getattr(request.scope.get("route"), "path", "unmatched")
        duration_seconds = time.perf_counter() - started_at
        request_metrics.record(
            method=request.method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http_request",
                "method": request.method,
                "route": route,
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 2),
            },
        )
        return response
    except Exception as error:
        duration_seconds = time.perf_counter() - started_at
        route = getattr(request.scope.get("route"), "path", "unmatched")
        request_metrics.record(
            method=request.method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        logger.error(
            "HTTP request failed",
            extra={
                "event": "http_request_failed",
                "method": request.method,
                "route": route,
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 2),
                "exception_type": type(error).__name__,
            },
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )
    finally:
        request_id_context.reset(context_token)

@app.get("/")
def root():
    return {
        "message": "AI Production and Hardware API is running"
    }