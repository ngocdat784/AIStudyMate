from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ai_audit_context import AIAuditContext
from app.services.gemini_retry import call_gemini_with_retry
from app.services.manufacturing_analytics_engine import (
    analyze_manufacturing_dataset,
)
from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    normalize_manufacturing_dataframe,
)


logger = logging.getLogger(__name__)
_SUPPORTED_MODULES = (
    "data_understanding",
    "validation",
    "kpi_calculation",
    "statistical_analysis",
    "trend_analysis",
    "correlation_analysis",
    "anomaly_detection",
    "bottleneck_screening",
    "evidence_generation",
)


class EvidenceBoundStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class EvidenceBoundAIInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: EvidenceBoundStatement
    root_cause_hypotheses: list[EvidenceBoundStatement] = Field(max_length=10)
    recommendations: list[EvidenceBoundStatement] = Field(max_length=10)
    limitations: list[str] = Field(max_length=10)


class UnifiedAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    analysis_type: Literal["manufacturing"] = "manufacturing"
    request_classification: dict[str, Any]
    execution_plan: list[str]
    executed_modules: list[str]
    executive_summary: str
    executive_summary_evidence_ids: list[str] = Field(default_factory=list)
    data_quality: dict[str, Any]
    kpi_results: dict[str, Any]
    statistical_findings: list[dict[str, Any]]
    time_series: list[dict[str, Any]]
    detected_anomalies: list[dict[str, Any]]
    potential_bottlenecks: list[dict[str, Any]]
    root_cause_hypotheses: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    confidence_assessment: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    analysis_limitations: list[str]
    ai_status: Literal[
        "not_requested",
        "not_configured",
        "available",
        "error",
        "validation_failed",
        "insufficient_evidence",
    ]
    ai_error: str | None = None
    ai_interpretation: dict[str, Any] | None = None


def _classify_request(
    question: str | None,
    source_filename: str | None,
) -> dict[str, Any]:
    normalized_question = (question or "").strip().casefold()
    detected: list[str] = []
    keyword_groups = {
        "quality": ("quality", "chất lượng", "defect", "lỗi"),
        "trend": ("trend", "xu hướng", "theo thời gian", "biến động"),
        "correlation": ("correlation", "tương quan", "liên hệ"),
        "anomaly": ("anomaly", "bất thường", "outlier", "đột biến"),
        "bottleneck": ("bottleneck", "nút thắt", "điểm nghẽn"),
        "kpi": ("kpi", "sản lượng", "downtime", "hiệu suất"),
    }
    for analysis_type, keywords in keyword_groups.items():
        if any(keyword in normalized_question for keyword in keywords):
            detected.append(analysis_type)
    intent = detected[0] if len(detected) == 1 else (
        "combined" if detected else "general"
    )
    suffix = (source_filename or "").rsplit(".", maxsplit=1)
    extension = f".{suffix[-1].casefold()}" if len(suffix) == 2 else ""
    input_type = {
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".xlsm": "xlsx",
        ".xls": "spreadsheet",
    }.get(extension, "dataframe")
    return {
        "intent": intent,
        "detected_topics": detected,
        "input_type": input_type,
        "question": (question or "").strip() or None,
    }


def _execution_plan(classification: dict[str, Any]) -> list[str]:
    topics = set(classification["detected_topics"])
    if not topics:
        return list(_SUPPORTED_MODULES)

    selected = {
        "data_understanding",
        "validation",
        "kpi_calculation",
        "evidence_generation",
    }
    if topics.intersection({"quality", "kpi"}):
        selected.add("statistical_analysis")
    if "trend" in topics:
        selected.add("trend_analysis")
    if "correlation" in topics:
        selected.update({"correlation_analysis", "statistical_analysis"})
    if "anomaly" in topics:
        selected.add("anomaly_detection")
    if "bottleneck" in topics:
        selected.add("bottleneck_screening")
    return [module for module in _SUPPORTED_MODULES if module in selected]


def _validate_ai_evidence(
    interpretation: EvidenceBoundAIInterpretation,
    evidence_ids: set[str],
) -> None:
    statements = [
        interpretation.executive_summary,
        *interpretation.root_cause_hypotheses,
        *interpretation.recommendations,
    ]
    for statement in statements:
        if not set(statement.evidence_ids).issubset(evidence_ids):
            raise ValueError("Gemini tham chiếu evidence ID không tồn tại.")
        if re.search(r"\d", statement.text):
            raise ValueError(
                "Gemini đưa số liệu vào văn bản thay vì tham chiếu evidence."
            )
    if any(re.search(r"\d", limitation) for limitation in interpretation.limitations):
        raise ValueError(
            "Gemini đưa số liệu vào phần giới hạn không có evidence."
        )


def _interpret_with_gemini(
    *,
    analytics: dict[str, Any],
    classification: dict[str, Any],
) -> tuple[EvidenceBoundAIInterpretation | None, str, str | None]:
    if not settings.GEMINI_API_KEY:
        return (
            None,
            "not_configured",
            "Gemini chưa được cấu hình; kết quả tính toán Python vẫn khả dụng.",
        )
    evidence = analytics["evidence_references"]
    evidence_ids = {item["id"] for item in evidence}
    if not evidence_ids:
        return None, "insufficient_evidence", "Không có evidence để gửi cho Gemini."

    from app.services.hardware_ai_service import (
        GEMINI_MODEL,
        _gemini_response_schema,
        get_client,
    )

    model = EvidenceBoundAIInterpretation
    prompt_context = {
        "request": classification,
        "kpi_results": analytics["kpi_results"],
        "statistical_findings": analytics["statistical_findings"],
        "detected_anomalies": analytics["detected_anomalies"],
        "potential_bottlenecks": analytics["potential_bottlenecks"],
        "evidence_references": evidence,
        "analysis_limitations": analytics["analysis_limitations"],
    }
    prompt = (
        "Diễn giải kết quả phân tích sản xuất bằng tiếng Việt. Chỉ sử dụng "
        "KPI, kết quả và evidence do Python cung cấp; không tính lại, không "
        "tạo dữ liệu, số liệu, ngày tháng, đơn vị, nguyên nhân hay ngưỡng mới. "
        "Mỗi summary, hypothesis và recommendation phải tham chiếu evidence "
        "IDs tồn tại. Root cause chỉ là giả thuyết cần kiểm chứng, không kết "
        "luận nhân quả. Không viết chữ số trong nội dung; dẫn chứng bằng ID "
        "trong trường evidence_ids. Nêu giới hạn dữ liệu. Trả đúng JSON schema.\n"
        + json.dumps(prompt_context, ensure_ascii=False, allow_nan=False)
    )
    audit = AIAuditContext(
        analysis_type="manufacturing_intelligence",
        model_name=GEMINI_MODEL,
        prompt_version="manufacturing_intelligence_v1",
        input_data=prompt_context,
        prompt_text=prompt,
    )
    try:
        response = call_gemini_with_retry(
            lambda: get_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": _gemini_response_schema(model),
                    "temperature": 0.2,
                },
            )
        )
    except Exception as error:
        audit.fail(error)
        logger.exception("Manufacturing intelligence Gemini request failed")
        return (
            None,
            "error",
            "Gemini không khả dụng; kết quả tính toán Python vẫn được giữ.",
        )

    audit.set_usage_from_response(response)
    raw_response = getattr(response, "text", None)
    audit.set_raw_response(raw_response)
    try:
        if not raw_response or not raw_response.strip():
            raise ValueError("Gemini không trả về nội dung.")
        interpretation = model.model_validate_json(raw_response)
        _validate_ai_evidence(interpretation, evidence_ids)
    except Exception as error:
        audit.fail(error, validation_failed=True)
        logger.warning(
            "Manufacturing intelligence Gemini output failed validation: %s",
            type(error).__name__,
        )
        return (
            None,
            "validation_failed",
            "Phản hồi Gemini không đạt kiểm định evidence; kết quả Python vẫn khả dụng.",
        )

    validated = interpretation.model_dump(mode="json")
    audit.succeed(validated)
    return interpretation, "available", None


def orchestrate_manufacturing_analysis(
    dataframe: pd.DataFrame,
    *,
    source_filename: str | None = None,
    question: str | None = None,
    column_mapping: dict[str, str] | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    production_unit: str = "unit",
    include_ai: bool = True,
    require_production: bool = True,
) -> dict[str, Any]:
    dataset: ManufacturingDataset = normalize_manufacturing_dataframe(
        dataframe,
        column_mapping=column_mapping,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        production_unit=production_unit,
        require_production=require_production,
    )
    classification = _classify_request(question, source_filename)
    plan = _execution_plan(classification)
    analytics = analyze_manufacturing_dataset(dataset, modules=plan)

    ai_interpretation: EvidenceBoundAIInterpretation | None = None
    ai_status = "not_requested"
    ai_error = None
    if include_ai:
        ai_interpretation, ai_status, ai_error = _interpret_with_gemini(
            analytics=analytics,
            classification=classification,
        )
        if ai_interpretation is not None:
            analytics["root_cause_hypotheses"] = [
                {
                    "hypothesis": statement.text,
                    "evidence_ids": statement.evidence_ids,
                    "status": "unverified_hypothesis",
                }
                for statement in ai_interpretation.root_cause_hypotheses
            ]
            analytics["recommendations"] = [
                {
                    "action": statement.text,
                    "evidence_ids": statement.evidence_ids,
                    "priority": "medium",
                }
                for statement in ai_interpretation.recommendations
            ]
            analytics["analysis_limitations"] = list(
                dict.fromkeys(
                    [
                        *analytics["analysis_limitations"],
                        *ai_interpretation.limitations,
                    ]
                )
            )
            analytics["executive_summary_evidence_ids"] = ai_interpretation.executive_summary.evidence_ids
            analytics["executive_summary"] = (
                ai_interpretation.executive_summary.text
            )

    result = UnifiedAnalysisResult(
        request_classification=classification,
        execution_plan=plan,
        ai_status=ai_status,
        ai_error=ai_error,
        ai_interpretation=(
            ai_interpretation.model_dump(mode="json")
            if ai_interpretation is not None
            else None
        ),
        **analytics,
    )
    serialized = result.model_dump(mode="json")
    serialized["data_contract"] = dataset.model_dump(mode="json", exclude={"records"})
    return serialized
