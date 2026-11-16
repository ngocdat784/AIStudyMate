from __future__ import annotations

import logging
import time
from threading import Lock
from decimal import Decimal
from typing import Any

from app.config import settings
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
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.estimated_cost_usd: float | None = None
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

    def set_usage_from_response(self, response: Any) -> None:
        if self.finalized:
            return
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            usage = getattr(response, "usage", None)
        if usage is None:
            return

        def token_value(*names: str) -> int | None:
            for name in names:
                value = (
                    usage.get(name)
                    if isinstance(usage, dict)
                    else getattr(usage, name, None)
                )
                if isinstance(value, int) and not isinstance(value, bool):
                    return max(value, 0)
            return None

        input_tokens = token_value(
            "prompt_token_count",
            "input_tokens",
            "prompt_tokens",
        )
        output_tokens = token_value(
            "candidates_token_count",
            "output_tokens",
            "completion_tokens",
        )
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

        input_price = settings.GEMINI_INPUT_COST_PER_MILLION_TOKENS_USD
        output_price = settings.GEMINI_OUTPUT_COST_PER_MILLION_TOKENS_USD
        if (
            input_tokens is None
            or output_tokens is None
            or input_price is None
            or output_price is None
        ):
            return
        estimated = (
            Decimal(input_tokens) * Decimal(str(input_price))
            + Decimal(output_tokens) * Decimal(str(output_price))
        ) / Decimal(1_000_000)
        self.estimated_cost_usd = float(estimated.quantize(Decimal("0.000001")))

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
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                estimated_cost_usd=self.estimated_cost_usd,
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
