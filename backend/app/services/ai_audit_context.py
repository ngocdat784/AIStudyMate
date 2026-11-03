from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any

from app.services.ai_audit_service import (
    finish_ai_audit_log,
    start_ai_audit_log,
)

logger = logging.getLogger(__name__)


class AIAuditContext:
    def __init__(
        self,
        *,
        analysis_type: str,
        model_name: str,
        prompt_version: str,
        input_data: dict[str, Any] | None = None,
        prompt_text: str | None = None,
    ) -> None:
        self.audit_id: int | None = None
        self.raw_response: str | None = None
        self.finalized = False
        self._finalize_lock = Lock()

        try:
            self.audit_id = start_ai_audit_log(
                analysis_type=analysis_type,
                model_name=model_name,
                prompt_version=prompt_version,
                input_data=input_data,
                prompt_text=prompt_text,
            )
        except Exception:
            logger.exception("Could not initialize AI audit context")

        self.started_at = time.perf_counter()

    def set_raw_response(self, raw_response: str | None) -> None:
        if not self.finalized:
            self.raw_response = raw_response

    def _finalize(
        self,
        *,
        status: str,
        validation_status: str,
        validated_result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> bool:
        with self._finalize_lock:
            if self.finalized:
                return False
            self.finalized = True

        try:
            duration_ms = max(
                0,
                int((time.perf_counter() - self.started_at) * 1000),
            )
            return finish_ai_audit_log(
                self.audit_id,
                status=status,
                validation_status=validation_status,
                raw_response=self.raw_response,
                validated_result=validated_result,
                error=error,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception("Could not finalize AI audit context")
            return False

    def succeed(
        self,
        validated_result: dict[str, Any],
    ) -> bool:
        return self._finalize(
            status="success",
            validation_status="passed",
            validated_result=validated_result,
        )

    def fail(
        self,
        error: Exception,
        *,
        validation_failed: bool = False,
    ) -> bool:
        if validation_failed:
            return self._finalize(
                status="validation_failed",
                validation_status="failed",
                error=error,
            )
        return self._finalize(
            status="error",
            validation_status="not_run",
            error=error,
        )
