from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ai_audit_context import AIAuditContext
from app.services.gemini_retry import call_gemini_with_retry
from app.services.manufacturing_data_foundation import (
    ManufacturingDataset,
    ManufacturingRecord,
    normalize_manufacturing_dataframe,
)


logger = logging.getLogger(__name__)
_MATERIAL_ANSWER_TOPICS = {
    "highest_material_loss",
    "defective_lots",
    "unstable_suppliers",
    "consumption_over_plan",
    "production_risk",
}


class MaterialAIStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class MaterialAIAnswer(MaterialAIStatement):
    topic: Literal[
        "highest_material_loss",
        "defective_lots",
        "unstable_suppliers",
        "consumption_over_plan",
        "production_risk",
    ]


class MaterialAIInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: MaterialAIStatement
    answers: list[MaterialAIAnswer] = Field(max_length=5)
    root_cause_hypotheses: list[MaterialAIStatement] = Field(max_length=10)
    recommendations: list[MaterialAIStatement] = Field(max_length=10)
    limitations: list[str] = Field(max_length=10)


def _rounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _quality_values(record: ManufacturingRecord) -> tuple[float, float] | None:
    if (
        record.defective_units is not None
        and record.inspected_quantity is not None
        and record.inspected_quantity > 0
    ):
        return float(record.defective_units), float(record.inspected_quantity)
    if record.defects is not None and record.production > 0:
        return float(record.defects), float(record.production)
    if (record.defect_type or record.defect) and record.production > 0:
        return 1.0, float(record.production)
    return None


def _group_key(
    record: ManufacturingRecord,
    dimensions: tuple[str, ...],
) -> tuple[str | None, ...]:
    return tuple(getattr(record, dimension) for dimension in dimensions)


def _group_records(
    records: list[ManufacturingRecord],
    dimensions: tuple[str, ...],
) -> dict[tuple[str | None, ...], list[ManufacturingRecord]]:
    grouped: dict[tuple[str | None, ...], list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        key = _group_key(record, dimensions)
        if any(value for value in key):
            grouped[key].append(record)
    return grouped


def _summarize_material(
    records: list[ManufacturingRecord],
    *,
    material: str,
    unit: str | None,
    currency: str | None,
) -> dict[str, Any]:
    planned_records = [
        record
        for record in records
        if unit is not None
        and record.planned_consumption is not None
        and record.actual_consumption is not None
    ]
    planned = (
        sum(float(record.planned_consumption or 0) for record in planned_records)
        if planned_records
        else None
    )
    actual_paired = (
        sum(float(record.actual_consumption or 0) for record in planned_records)
        if planned_records
        else None
    )
    variance = actual_paired - planned if actual_paired is not None and planned is not None else None
    variance_percent = (
        variance / planned * 100
        if variance is not None and planned is not None and planned > 0
        else None
    )

    actual_records = [
        record
        for record in records
        if unit is not None and record.actual_consumption is not None
    ]
    actual_consumption = (
        sum(float(record.actual_consumption or 0) for record in actual_records)
        if actual_records
        else None
    )
    usage_records = [
        record
        for record in actual_records
        if record.production > 0
    ]
    usage_rate = (
        sum(float(record.actual_consumption or 0) for record in usage_records)
        / sum(record.production for record in usage_records)
        if usage_records and sum(record.production for record in usage_records) > 0
        else None
    )

    loss_records: list[tuple[ManufacturingRecord, float]] = []
    for record in records:
        loss = (
            record.material_loss_quantity
            if record.material_loss_quantity is not None
            else record.scrap_material_quantity
        )
        if unit is not None and record.actual_consumption is not None and loss is not None:
            loss_records.append((record, float(loss)))
    loss_quantity = (
        sum(loss for _, loss in loss_records) if loss_records else None
    )
    loss_denominator = (
        sum(float(record.actual_consumption or 0) for record, _ in loss_records)
        if loss_records
        else 0.0
    )
    material_yield = (
        (loss_denominator - loss_quantity) / loss_denominator * 100
        if loss_quantity is not None
        and loss_denominator > 0
        and loss_quantity <= loss_denominator
        else None
    )
    waste_rate = (
        loss_quantity / loss_denominator * 100
        if loss_quantity is not None
        and loss_denominator > 0
        and loss_quantity <= loss_denominator
        else None
    )
    scrap_records = [
        record
        for record in records
        if unit is not None and record.scrap_material_quantity is not None
    ]
    scrap_quantity = (
        sum(float(record.scrap_material_quantity or 0) for record in scrap_records)
        if scrap_records
        else None
    )

    costs: list[float] = []
    planned_costs: list[float] = []
    for record in records:
        if record.material_cost is not None:
            costs.append(float(record.material_cost))
        elif (
            unit is not None
            and
            record.actual_consumption is not None
            and record.material_unit_cost is not None
        ):
            costs.append(
                float(record.actual_consumption) * float(record.material_unit_cost)
            )
        if (
            unit is not None
            and
            record.planned_consumption is not None
            and record.material_unit_cost is not None
        ):
            planned_costs.append(
                float(record.planned_consumption) * float(record.material_unit_cost)
            )

    quality_pairs = [
        pair for record in records if (pair := _quality_values(record)) is not None
    ]
    defect_count = sum(numerator for numerator, _ in quality_pairs) if quality_pairs else None
    inspected_count = sum(denominator for _, denominator in quality_pairs) if quality_pairs else None
    defect_rate = (
        defect_count / inspected_count * 100
        if defect_count is not None and inspected_count
        else None
    )
    production = sum(record.production for record in records)

    return {
        "material": material,
        "material_types": sorted(
            {record.material_type for record in records if record.material_type}
        ),
        "unit": unit,
        "currency": currency,
        "planned_consumption": _rounded(planned),
        "actual_consumption": _rounded(actual_consumption),
        "consumption_variance": _rounded(variance),
        "consumption_variance_percent": _rounded(variance_percent),
        "consumption_pair_count": len(planned_records),
        "material_usage_rate": _rounded(usage_rate),
        "usage_rate_unit": f"{unit or 'unknown'}/{records[0].production_unit}",
        "material_loss": _rounded(loss_quantity),
        "scrap_material": _rounded(scrap_quantity),
        "material_yield_percent": _rounded(material_yield),
        "waste_rate_percent": _rounded(waste_rate),
        "material_cost": _rounded(sum(costs) if costs else None),
        "planned_material_cost": _rounded(
            sum(planned_costs) if planned_costs else None
        ),
        "defects": _rounded(defect_count),
        "inspected_or_produced": _rounded(inspected_count),
        "material_defect_rate_percent": _rounded(defect_rate),
        "production": _rounded(production),
        "record_count": len(records),
        "material_lot_count": len(
            {record.material_lot for record in records if record.material_lot}
        ),
    }


def _material_summaries(
    records: list[ManufacturingRecord],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str | None, str | None], list[ManufacturingRecord]] = (
        defaultdict(list)
    )
    for record in records:
        if not record.material:
            continue
        currency = record.material_currency
        grouped[
            (
                record.material,
                record.material_unit,
                str(currency).strip().upper() if currency else None,
            )
        ].append(record)
    summaries = [
        _summarize_material(
            group_records,
            material=material,
            unit=unit,
            currency=currency,
        )
        for (material, unit, currency), group_records in grouped.items()
    ]
    return sorted(
        summaries,
        key=lambda item: (
            item["material"].casefold(),
            item["unit"] or "",
            item["currency"] or "",
        ),
    )


def _supplier_summaries(
    records: list[ManufacturingRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supplier_groups = _group_records(records, ("supplier",))
    supplier_rows: list[dict[str, Any]] = []
    trends: list[dict[str, Any]] = []
    monthly_pairs: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (supplier,), group_records in supplier_groups.items():
        pairs = [
            pair for record in group_records
            if (pair := _quality_values(record)) is not None
        ]
        defects = sum(numerator for numerator, _ in pairs) if pairs else None
        denominator = sum(value for _, value in pairs) if pairs else None
        rate = (
            defects / denominator * 100
            if defects is not None and denominator
            else None
        )
        materials = sorted(
            {record.material for record in group_records if record.material}
        )
        supplier_rows.append(
            {
                "supplier": supplier,
                "defects": _rounded(defects),
                "inspected_or_produced": _rounded(denominator),
                "defect_rate_percent": _rounded(rate),
                "quality_pair_count": len(pairs),
                "material_count": len(materials),
                "materials": materials,
                "record_count": len(group_records),
            }
        )
        for record in group_records:
            pair = _quality_values(record)
            if pair is None or record.timestamp is None:
                continue
            month = record.timestamp.strftime("%Y-%m")
            monthly_pairs[supplier][month].append(pair)

    supplier_rows.sort(
        key=lambda item: (
            item["defect_rate_percent"] is None,
            -(item["defect_rate_percent"] or 0),
            str(item["supplier"]).casefold(),
        )
    )
    for supplier, months in sorted(monthly_pairs.items()):
        for month, pairs in sorted(months.items()):
            defects = sum(numerator for numerator, _ in pairs)
            denominator = sum(total for _, total in pairs)
            trends.append(
                {
                    "supplier": supplier,
                    "period": month,
                    "defect_rate_percent": _rounded(
                        defects / denominator * 100 if denominator else None
                    ),
                    "record_count": len(pairs),
                }
            )

    evaluated = [
        item for item in supplier_rows if item["defect_rate_percent"] is not None
    ]
    peer_rates = [
        float(item["defect_rate_percent"]) for item in evaluated
    ]
    peer_median = statistics.median(peer_rates) if peer_rates else None
    peer_q3 = (
        statistics.quantiles(peer_rates, n=4, method="inclusive")[2]
        if len(peer_rates) >= 2
        else None
    )
    for item in supplier_rows:
        supplier_trend = [
            float(row["defect_rate_percent"])
            for row in trends
            if row["supplier"] == item["supplier"]
            and row["defect_rate_percent"] is not None
        ]
        volatility = (
            statistics.pstdev(supplier_trend)
            if len(supplier_trend) >= 2
            else None
        )
        item["monthly_volatility_percentage_points"] = _rounded(volatility)
        rate = item["defect_rate_percent"]
        if rate is None or item["quality_pair_count"] < 3:
            item["risk_level"] = "insufficient_data"
        elif (
            rate >= max(5.0, peer_q3 or 0.0)
            or (volatility is not None and volatility >= 5.0)
        ):
            item["risk_level"] = "high"
        elif (
            (peer_median is not None and rate > peer_median)
            or (volatility is not None and volatility >= 2.0)
        ):
            item["risk_level"] = "medium"
        else:
            item["risk_level"] = "low"
    return supplier_rows, trends


def _traceability(
    records: list[ManufacturingRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if not record.material_lot:
            continue
        key = (
            record.material_lot,
            record.material,
            record.supplier,
            record.product,
            record.process,
            record.batch,
            record.material_unit,
        )
        grouped[key].append(record)

    mappings: list[dict[str, Any]] = []
    suspect_lots: list[dict[str, Any]] = []
    for key, group_records in sorted(
        grouped.items(),
        key=lambda item: tuple(str(value or "").casefold() for value in item[0]),
    ):
        lot, material, supplier, product, process, production_batch, unit = key
        pairs = [
            pair for record in group_records
            if (pair := _quality_values(record)) is not None
        ]
        defects = sum(numerator for numerator, _ in pairs) if pairs else None
        denominator = sum(value for _, value in pairs) if pairs else None
        item = {
            "material_lot": lot,
            "material": material,
            "supplier": supplier,
            "product": product,
            "process": process,
            "production_batch": production_batch,
            "material_unit": unit,
            "actual_consumption": _rounded(
                sum(
                    record.actual_consumption or 0
                    for record in group_records
                    if record.actual_consumption is not None
                )
                if unit is not None
                and any(record.actual_consumption is not None for record in group_records)
                else None
            ),
            "defects": _rounded(defects),
            "inspected_or_produced": _rounded(denominator),
            "defect_rate_percent": _rounded(
                defects / denominator * 100
                if defects is not None and denominator
                else None
            ),
            "record_count": len(group_records),
            "source_rows": [record.source_row for record in group_records],
        }
        mappings.append(item)
        if defects is not None and defects > 0:
            suspect_lots.append(
                {
                    **item,
                    "interpretation": "association_only_not_causation",
                }
            )
    return mappings, sorted(
        suspect_lots,
        key=lambda item: (
            -(item["defect_rate_percent"] or 0),
            -(item["defects"] or 0),
            str(item["material_lot"]).casefold(),
        ),
    )


def _material_product_mapping(
    records: list[ManufacturingRecord],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[ManufacturingRecord]] = defaultdict(list)
    for record in records:
        if not record.material or not record.product:
            continue
        grouped[
            (
                record.material,
                record.supplier,
                record.product,
                record.process,
                record.batch,
                record.material_unit,
            )
        ].append(record)
    mappings: list[dict[str, Any]] = []
    for key, group_records in grouped.items():
        material, supplier, product, process, batch, unit = key
        quality = _rate_summary(group_records)
        measured_consumption = (
            sum(
                record.actual_consumption or 0
                for record in group_records
                if record.actual_consumption is not None
            )
            if unit is not None
            and any(record.actual_consumption is not None for record in group_records)
            else None
        )
        mappings.append(
            {
                "material": material,
                "supplier": supplier,
                "product": product,
                "process": process,
                "production_batch": batch,
                "material_unit": unit,
                "material_lots": sorted(
                    {
                        record.material_lot
                        for record in group_records
                        if record.material_lot
                    }
                ),
                "actual_consumption": _rounded(measured_consumption),
                "defects": quality["defects"],
                "defect_rate_percent": quality["defect_rate_percent"],
                "record_count": len(group_records),
            }
        )
    return sorted(
        mappings,
        key=lambda item: (
            str(item["material"]).casefold(),
            str(item["product"]).casefold(),
            str(item["supplier"] or "").casefold(),
        ),
    )


def _process_loss(records: list[ManufacturingRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str | None, str | None], list[ManufacturingRecord]] = (
        defaultdict(list)
    )
    for record in records:
        if not record.process or not record.material:
            continue
        grouped[(record.process, record.material, record.material_unit)].append(record)
    result: list[dict[str, Any]] = []
    for (process, material, unit), group_records in grouped.items():
        if unit is None:
            continue
        paired = [
            (
                float(record.actual_consumption),
                float(
                    record.material_loss_quantity
                    if record.material_loss_quantity is not None
                    else record.scrap_material_quantity
                ),
            )
            for record in group_records
            if record.actual_consumption is not None
            and (
                record.material_loss_quantity is not None
                or record.scrap_material_quantity is not None
            )
        ]
        if not paired:
            continue
        consumed = sum(actual for actual, _ in paired)
        loss = sum(waste for _, waste in paired)
        result.append(
            {
                "process": process,
                "material": material,
                "unit": unit,
                "actual_consumption": _rounded(consumed),
                "material_loss": _rounded(loss),
                "waste_rate_percent": _rounded(
                    loss / consumed * 100
                    if consumed > 0 and loss <= consumed
                    else None
                ),
                "record_count": len(group_records),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["waste_rate_percent"] is None,
            -(item["waste_rate_percent"] or 0),
            item["process"].casefold(),
        ),
    )


def _validate_ai_evidence(
    interpretation: MaterialAIInterpretation,
    evidence_ids: set[str],
) -> None:
    if {answer.topic for answer in interpretation.answers} != _MATERIAL_ANSWER_TOPICS:
        raise ValueError("Material AI must answer every required topic.")
    statements = [
        interpretation.executive_summary,
        *interpretation.answers,
        *interpretation.root_cause_hypotheses,
        *interpretation.recommendations,
    ]
    for statement in statements:
        if not set(statement.evidence_ids).issubset(evidence_ids):
            raise ValueError("Material AI referenced an unknown evidence ID.")
        if any(character.isdigit() for character in statement.text):
            raise ValueError("Material AI must link numeric claims by evidence ID.")
    if any(
        any(character.isdigit() for character in limitation)
        for limitation in interpretation.limitations
    ):
        raise ValueError("Material AI limitation contains an unsupported number.")


def _interpret_with_gemini(
    material: dict[str, Any],
) -> tuple[MaterialAIInterpretation | None, str, str | None]:
    if not settings.GEMINI_API_KEY:
        return (
            None,
            "not_configured",
            "Gemini chưa được cấu hình; kết quả vật liệu tính bằng Python vẫn khả dụng.",
        )
    evidence_ids = {item["id"] for item in material["evidence_references"]}
    if not evidence_ids:
        return None, "insufficient_evidence", "Không có evidence vật liệu."

    from app.services.hardware_ai_service import (
        GEMINI_MODEL,
        _gemini_response_schema,
        get_client,
    )

    context = {
        "questions": sorted(_MATERIAL_ANSWER_TOPICS),
        "kpi_results": material["kpi_results"],
        "material_performance": material["material_performance"],
        "supplier_quality": material["supplier_quality"],
        "supplier_quality_trend": material["supplier_quality_trend"],
        "material_traceability": material["material_traceability"],
        "defect_associated_lots": material["defect_associated_lots"],
        "process_material_loss": material["process_material_loss"],
        "management_answers": material["management_answers"],
        "evidence_references": material["evidence_references"],
        "analysis_limitations": material["analysis_limitations"],
    }
    prompt = (
        "Trả lời bằng tiếng Việt cho người quản lý sản xuất. Chỉ diễn giải "
        "các kết quả Python và evidence được cung cấp; không tự tính số, tạo "
        "benchmark hoặc suy diễn quan hệ nhân quả. Trả đủ đúng năm chủ đề "
        "highest_material_loss, defective_lots, unstable_suppliers, "
        "consumption_over_plan, production_risk. Mọi statement phải dẫn "
        "evidence ID và không viết chữ số trong nội dung. Lô có lỗi chỉ là "
        "mối liên hệ cần điều tra, không kết luận nguyên nhân. Nêu rõ khi dữ "
        "liệu chưa đủ. JSON:\n"
        + json.dumps(context, ensure_ascii=False, allow_nan=False)
    )
    audit = AIAuditContext(
        analysis_type="material_intelligence",
        model_name=GEMINI_MODEL,
        prompt_version="material_intelligence_v1",
        input_data=context,
        prompt_text=prompt,
    )
    try:
        response = call_gemini_with_retry(
            lambda: get_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": _gemini_response_schema(
                        MaterialAIInterpretation
                    ),
                    "temperature": 0.2,
                },
            )
        )
    except Exception as error:
        audit.fail(error)
        logger.exception("Material intelligence Gemini request failed")
        return (
            None,
            "error",
            "Gemini không khả dụng; giữ nguyên kết quả vật liệu tính bằng Python.",
        )

    audit.set_usage_from_response(response)
    raw_response = getattr(response, "text", None)
    audit.set_raw_response(raw_response)
    try:
        if not raw_response or not raw_response.strip():
            raise ValueError("Gemini không trả về material interpretation.")
        interpretation = MaterialAIInterpretation.model_validate_json(raw_response)
        _validate_ai_evidence(interpretation, evidence_ids)
    except Exception as error:
        audit.fail(error, validation_failed=True)
        logger.warning(
            "Material intelligence response failed validation: %s",
            type(error).__name__,
        )
        return (
            None,
            "validation_failed",
            "Diễn giải Gemini không đạt kiểm định evidence; kết quả Python vẫn dùng được.",
        )
    audit.succeed(interpretation.model_dump(mode="json"))
    return interpretation, "success", None


def _add_evidence(
    evidence: list[dict[str, Any]],
    *,
    metric: str,
    value: Any,
    unit: str,
    method: str,
    record_count: int,
    dimensions: dict[str, str] | None = None,
) -> str:
    evidence_id = f"E{len(evidence) + 1:04d}"
    item: dict[str, Any] = {
        "id": evidence_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "method": method,
        "record_count": record_count,
    }
    if dimensions:
        item["dimensions"] = dimensions
    evidence.append(item)
    return evidence_id


def _rate_summary(records: list[ManufacturingRecord]) -> dict[str, Any]:
    pairs = [
        pair for record in records
        if (pair := _quality_values(record)) is not None
    ]
    defects = sum(numerator for numerator, _ in pairs) if pairs else None
    denominator = sum(value for _, value in pairs) if pairs else None
    return {
        "defects": _rounded(defects),
        "inspected_or_produced": _rounded(denominator),
        "defect_rate_percent": _rounded(
            defects / denominator * 100
            if defects is not None and denominator
            else None
        ),
        "pair_count": len(pairs),
    }


def analyze_material_intelligence(
    dataframe: pd.DataFrame,
    *,
    source_timezone: str = "UTC",
    production_unit: str = "unit",
    column_mapping: dict[str, str] | None = None,
    include_ai: bool = False,
) -> dict[str, Any]:
    dataset: ManufacturingDataset = normalize_manufacturing_dataframe(
        dataframe,
        source_timezone=source_timezone,
        production_unit=production_unit,
        column_mapping=column_mapping,
        require_production=False,
    )
    records = dataset.records
    evidence: list[dict[str, Any]] = []
    material_performance = _material_summaries(records)
    supplier_quality, supplier_quality_trend = _supplier_summaries(records)
    material_traceability, defect_associated_lots = _traceability(records)
    material_product_mapping = _material_product_mapping(records)
    process_material_loss = _process_loss(records)
    currency_groups = {
        (
            str(record.material_currency or "").strip().upper()
            or None
        )
        for record in records
        if record.material_cost is not None
        or (
            record.actual_consumption is not None
            and record.material_unit_cost is not None
        )
    }
    consumption_units = {
        record.material_unit
        for record in records
        if record.actual_consumption is not None
    }
    total_actual = (
        sum(
            record.actual_consumption or 0
            for record in records
            if record.actual_consumption is not None
        )
        if len(consumption_units) == 1
        and consumption_units
        and None not in consumption_units
        else None
    )
    planned_pairs = [
        record
        for record in records
        if record.planned_consumption is not None
        and record.actual_consumption is not None
    ]
    paired_units = {record.material_unit for record in planned_pairs}
    comparable_planned_pairs = (
        planned_pairs
        if (
            len(paired_units) == 1
            and None not in paired_units
            and consumption_units == paired_units
        )
        else []
    )
    paired_planned = (
        sum(record.planned_consumption or 0 for record in comparable_planned_pairs)
        if comparable_planned_pairs
        else None
    )
    total_planned = (
        sum(
            record.planned_consumption or 0
            for record in comparable_planned_pairs
        )
        if comparable_planned_pairs
        else None
    )
    paired_actual = (
        sum(record.actual_consumption or 0 for record in comparable_planned_pairs)
        if comparable_planned_pairs
        else None
    )
    variance = (
        paired_actual - paired_planned
        if paired_actual is not None and paired_planned is not None
        else None
    )
    variance_percent = (
        variance / paired_planned * 100
        if variance is not None and paired_planned and paired_planned > 0
        else None
    )
    usage_pairs = [
        record
        for record in records
        if record.material_unit is not None
        and record.actual_consumption is not None
        and record.production > 0
    ]
    usage_units = {record.material_unit for record in usage_pairs}
    usage_rate = (
        sum(record.actual_consumption or 0 for record in usage_pairs)
        / sum(record.production for record in usage_pairs)
        if usage_pairs and len(usage_units) == 1
        else None
    )
    loss_pairs = [
        (
            float(record.actual_consumption),
            float(
                record.material_loss_quantity
                if record.material_loss_quantity is not None
                else record.scrap_material_quantity
            ),
        )
        for record in records
        if record.actual_consumption is not None
        and (
            record.material_loss_quantity is not None
            or record.scrap_material_quantity is not None
        )
    ]
    loss_units = {
        record.material_unit
        for record in records
        if record.actual_consumption is not None
        and (
            record.material_loss_quantity is not None
            or record.scrap_material_quantity is not None
        )
    }
    loss_unit_compatible = (
        len(loss_units) == 1 and None not in loss_units
    )
    total_loss = (
        sum(loss for _, loss in loss_pairs)
        if loss_pairs and loss_unit_compatible
        else None
    )
    loss_input = sum(consumed for consumed, _ in loss_pairs) if loss_pairs else 0.0
    yield_percent = (
        (loss_input - total_loss) / loss_input * 100
        if total_loss is not None and loss_input > 0 and total_loss <= loss_input
        else None
    )
    waste_rate = (
        total_loss / loss_input * 100
        if total_loss is not None and loss_input > 0 and total_loss <= loss_input
        else None
    )
    explicit_scrap = [
        record.scrap_material_quantity
        for record in records
        if record.scrap_material_quantity is not None
    ]
    scrap_units = {
        record.material_unit
        for record in records
        if record.scrap_material_quantity is not None
    }
    scrap_total = (
        sum(explicit_scrap)
        if explicit_scrap
        and len(scrap_units) == 1
        and None not in scrap_units
        else None
    )
    actual_costs = [
        float(record.material_cost)
        if record.material_cost is not None
        else float(record.actual_consumption) * float(record.material_unit_cost)
        for record in records
        if record.material_cost is not None
        or (
            record.material_unit is not None
            and
            record.actual_consumption is not None
            and record.material_unit_cost is not None
        )
    ]
    planned_costs = [
        float(record.planned_consumption) * float(record.material_unit_cost)
        for record in records
        if record.material_unit is not None
        and record.planned_consumption is not None
        and record.material_unit_cost is not None
    ]
    total_cost = (
        sum(actual_costs)
        if actual_costs and len(currency_groups) == 1
        and None not in currency_groups
        else None
    )
    planned_cost = (
        sum(planned_costs)
        if planned_costs and len(currency_groups) == 1
        and None not in currency_groups
        else None
    )
    overall_quality = _rate_summary(records)
    main_unit = next(iter(consumption_units)) if len(consumption_units) == 1 else None
    currency = next(iter(currency_groups)) if len(currency_groups) == 1 else None

    kpi_values: dict[str, tuple[Any, str, int, str]] = {
        "planned_consumption": (
            total_planned,
            main_unit or "mixed_or_unknown_unit",
            len(comparable_planned_pairs),
            "sum_of_planned_consumption_paired_with_actual_same_unit",
        ),
        "actual_consumption": (
            total_actual,
            main_unit or "mixed_or_unknown_unit",
            len([record for record in records if record.actual_consumption is not None]),
            "sum_of_explicit_actual_consumption_same_unit",
        ),
        "consumption_variance": (
            _rounded(variance),
            main_unit or "mixed_or_unknown_unit",
            len(comparable_planned_pairs),
            "paired_actual_minus_planned",
        ),
        "consumption_variance_percent": (
            _rounded(variance_percent),
            "percent",
            len(comparable_planned_pairs),
            "paired_variance_divided_by_paired_plan",
        ),
        "material_usage_rate": (
            _rounded(usage_rate),
            f"{main_unit or 'material_unit'}/{dataset.units['production']}",
            len(usage_pairs),
            "paired_actual_consumption_divided_by_production",
        ),
        "material_loss": (
            _rounded(total_loss),
            main_unit or "mixed_or_unknown_unit",
            len(loss_pairs),
            "explicit_material_loss_or_scrap",
        ),
        "scrap_material": (
            _rounded(scrap_total),
            main_unit or "mixed_or_unknown_unit",
            len(explicit_scrap),
            "sum_of_explicit_scrap_material",
        ),
        "material_yield": (
            _rounded(yield_percent),
            "percent",
            len(loss_pairs),
            "actual_consumption_minus_loss_divided_by_consumption",
        ),
        "waste_rate": (
            _rounded(waste_rate),
            "percent",
            len(loss_pairs),
            "explicit_loss_or_scrap_divided_by_consumption",
        ),
        "material_cost": (
            _rounded(total_cost),
            currency or "currency_unknown",
            len(actual_costs),
            "explicit_cost_or_actual_consumption_times_unit_cost",
        ),
        "planned_material_cost": (
            _rounded(planned_cost),
            currency or "currency_unknown",
            len(planned_costs),
            "planned_consumption_times_unit_cost",
        ),
        "supplier_defect_rate": (
            overall_quality["defect_rate_percent"],
            "percent",
            overall_quality["pair_count"],
            "paired_defects_divided_by_inspected_or_produced_quantity",
        ),
    }
    kpi_results: dict[str, dict[str, Any]] = {}
    for metric, (value, unit, count, method) in kpi_values.items():
        evidence_id = (
            _add_evidence(
                evidence,
                metric=metric,
                value=value,
                unit=unit,
                method=method,
                record_count=count,
            )
            if value is not None
            else None
        )
        kpi_results[metric] = {
            "value": value,
            "unit": unit,
            "basis": method,
            "record_count": count,
            "evidence_id": evidence_id,
        }

    for item in material_performance:
        item["evidence_ids"] = {}
        for metric, field, unit in (
            ("material_loss", "material_loss", item["unit"] or "unknown"),
            ("material_yield", "material_yield_percent", "percent"),
            ("consumption_variance", "consumption_variance", item["unit"] or "unknown"),
            ("material_cost", "material_cost", item["currency"] or "currency_unknown"),
            ("material_defect_rate", "material_defect_rate_percent", "percent"),
        ):
            if item[field] is None:
                continue
            item["evidence_ids"][metric] = _add_evidence(
                evidence,
                metric=metric,
                value=item[field],
                unit=unit,
                method="material_group_aggregation",
                record_count=item["record_count"],
                dimensions={"material": item["material"]},
            )
    for item in supplier_quality:
        item["evidence_id"] = (
            _add_evidence(
                evidence,
                metric="supplier_defect_rate",
                value=item["defect_rate_percent"],
                unit="percent",
                method="paired_defects_divided_by_inspected_or_produced_quantity",
                record_count=item["quality_pair_count"],
                dimensions={"supplier": str(item["supplier"])},
            )
            if item["defect_rate_percent"] is not None
            else None
        )
    for item in material_traceability:
        if item["defects"] is not None:
            item["evidence_id"] = _add_evidence(
                evidence,
                metric="lot_defects",
                value=item["defects"],
                unit="defects",
                method="defect_count_linked_to_material_lot",
                record_count=item["record_count"],
                dimensions={"material_lot": str(item["material_lot"])},
            )
        else:
            item["evidence_id"] = None
    for suspect in defect_associated_lots:
        matching_mapping = next(
            (
                item for item in material_traceability
                if item["source_rows"] == suspect["source_rows"]
            ),
            None,
        )
        suspect["evidence_id"] = (
            matching_mapping["evidence_id"] if matching_mapping else None
        )
    for item in process_material_loss:
        item["evidence_id"] = (
            _add_evidence(
                evidence,
                metric="material_loss",
                value=item["material_loss"],
                unit=item["unit"],
                method="explicit_loss_or_scrap_by_process",
                record_count=item["record_count"],
                dimensions={
                    "process": item["process"],
                    "material": item["material"],
                },
            )
            if item["material_loss"] is not None
            else None
        )

    highest_losses_by_unit: dict[str, dict[str, Any]] = {}
    for item in material_performance:
        if item["material_loss"] is None:
            continue
        unit = item["unit"]
        if unit is None:
            continue
        current = highest_losses_by_unit.get(unit)
        if current is None or item["material_loss"] > current["material_loss"]:
            highest_losses_by_unit[unit] = item

    highest_lot = defect_associated_lots[0] if defect_associated_lots else None
    high_risk_supplier = next(
        (
            item for item in supplier_quality
            if item["risk_level"] in {"high", "medium"}
        ),
        None,
    )
    over_plan = next(
        (
            item for item in sorted(
                material_performance,
                key=lambda row: -(row["consumption_variance_percent"] or 0),
            )
            if item["consumption_variance_percent"] is not None
            and item["consumption_variance_percent"] > 0
        ),
        None,
    )
    risky_material = max(
        (
            item for item in material_performance
            if item["waste_rate_percent"] is not None
            and item["waste_rate_percent"] > 0
        ),
        key=lambda item: item["waste_rate_percent"] or 0,
        default=None,
    )
    def _format_loss(value: float) -> str:
        return f"{value:g}"

    management_answers: dict[str, dict[str, Any]] = {
        "highest_material_loss": (
            {
                "answer": (
                    "Trong dữ liệu chỉ có một đơn vị dùng chung: "
                    f"{next(iter(highest_losses_by_unit.values()))['material']} "
                    "có hao hụt cao nhất trong cùng đơn vị "
                    f"{next(iter(highest_losses_by_unit.values()))['unit']}, "
                    "với mức ghi nhận "
                    f"{_format_loss(next(iter(highest_losses_by_unit.values()))['material_loss'])} "
                    f"{next(iter(highest_losses_by_unit.values()))['unit']}."
                ),
                "evidence_ids": [
                    next(iter(highest_losses_by_unit.values()))["evidence_ids"]["material_loss"]
                ],
            }
            if len(highest_losses_by_unit) == 1
            else (
                {
                    "answer": (
                        "Không có cơ sở quy đổi để xếp hạng vật liệu trên toàn bộ "
                        "đơn vị. Hao hụt cao nhất theo từng đơn vị: "
                        + "; ".join(
                            (
                                f"{item['material']} ghi nhận "
                                f"{_format_loss(item['material_loss'])} {unit}"
                            )
                            for unit, item in sorted(highest_losses_by_unit.items())
                        )
                        + "."
                    ),
                    "evidence_ids": [
                        item["evidence_ids"]["material_loss"]
                        for item in highest_losses_by_unit.values()
                        if item["evidence_ids"].get("material_loss")
                    ],
                }
                if len(highest_losses_by_unit) > 1
                else {"answer": "Chưa đủ dữ liệu hao hụt vật liệu.", "evidence_ids": []}
            )
        ),
        "defective_lots": (
            {
                "answer": (
                    f"Lô {highest_lot['material_lot']} được ghi nhận "
                    f"{highest_lot['defects']} lỗi; đây là liên hệ cần điều tra, "
                    "không phải kết luận nguyên nhân."
                ),
                "evidence_ids": [highest_lot["evidence_id"]]
                if highest_lot["evidence_id"]
                else [],
            }
            if highest_lot
            else {"answer": "Chưa có lô liên kết với bản ghi lỗi.", "evidence_ids": []}
        ),
        "unstable_suppliers": (
            {
                "answer": (
                    f"{high_risk_supplier['supplier']} có mức rủi ro "
                    f"{high_risk_supplier['risk_level']} theo ngưỡng sàng lọc."
                ),
                "evidence_ids": [high_risk_supplier["evidence_id"]]
                if high_risk_supplier["evidence_id"]
                else [],
            }
            if high_risk_supplier
            else {"answer": "Chưa đủ dữ liệu để xếp hạng rủi ro nhà cung cấp.", "evidence_ids": []}
        ),
        "consumption_over_plan": (
            {
                "answer": (
                    f"{over_plan['material']} vượt định mức "
                    f"{over_plan['consumption_variance_percent']}% trên các dòng "
                    "có cả Actual và Planned Consumption."
                ),
                "evidence_ids": [
                    over_plan["evidence_ids"]["consumption_variance"]
                ],
            }
            if over_plan
            and over_plan["evidence_ids"].get("consumption_variance")
            else {
                "answer": "Chưa đủ dữ liệu ghép cặp tiêu hao thực tế và định mức.",
                "evidence_ids": [],
            }
        ),
        "production_risk": (
            {
                "answer": (
                    f"{risky_material['material']} có hao hụt vật liệu cần "
                    "được theo dõi trong sản xuất."
                ),
                "evidence_ids": [
                    risky_material["evidence_ids"]["material_loss"]
                ]
                if risky_material["evidence_ids"].get("material_loss")
                else [],
            }
            if risky_material
            else {"answer": "Chưa có tín hiệu hao hụt đủ dữ liệu để sàng lọc.", "evidence_ids": []}
        ),
    }
    limitations: list[str] = []
    if not any(record.material for record in records):
        limitations.append("Không có cột Material; không thể phân nhóm vật liệu.")
    if not any(record.material_unit for record in records):
        limitations.append("Thiếu Material Unit; tiêu hao không thể chuẩn hóa hoặc so sánh giữa đơn vị.")
    if not any(record.planned_consumption is not None for record in records):
        limitations.append("Thiếu Planned Consumption; không thể so sánh tiêu hao với định mức.")
    if not any(record.actual_consumption is not None for record in records):
        limitations.append("Thiếu Actual Consumption; không thể tính usage rate hoặc tiêu hao thực tế.")
    if not any(
        record.material_loss_quantity is not None
        or record.scrap_material_quantity is not None
        for record in records
    ):
        limitations.append("Thiếu Material Loss/Scrap Material; yield và waste rate chưa thể tính.")
    if not any(record.material_lot for record in records):
        limitations.append("Thiếu Material Lot; không thể truy xuất lỗi đến lô nguyên liệu.")
    if not any(record.supplier for record in records):
        limitations.append("Thiếu Supplier; không thể xếp hạng chất lượng nhà cung cấp.")
    if not any(record.timestamp for record in records):
        limitations.append("Thiếu timestamp; không thể xây dựng xu hướng chất lượng nhà cung cấp.")
    if len(consumption_units) > 1:
        limitations.append(
            "Tiêu hao tổng thể có nhiều đơn vị chưa quy đổi chung; KPI tổng được để trống."
        )
    if any(
        record.material_unit
        and record.material_unit not in {"g", "mL", "piece"}
        for record in records
    ):
        limitations.append(
            "Đơn vị không thuộc nhóm quy đổi khối lượng/thể tích/số lượng; "
            "giá trị được giữ nguyên và không quy đổi chéo."
        )
    if any(
        record.material_cost is not None
        or record.material_unit_cost is not None
        for record in records
    ) and not currency:
        limitations.append(
            "Không có mã tiền tệ; chi phí chỉ được cộng khi dữ liệu có cùng ngữ nghĩa tiền tệ."
        )
    if defect_associated_lots:
        limitations.append(
            "Lot-to-defect mapping chỉ thể hiện liên hệ trong dữ liệu, không chứng minh nguyên nhân."
        )
    limitations.extend(dataset.data_quality.warnings)

    ai_interpretation: dict[str, Any] | None = None
    ai_error: str | None = None
    ai_status = "not_requested"
    if include_ai:
        interpretation, ai_status, ai_error = _interpret_with_gemini(
            {
                "kpi_results": kpi_results,
                "material_performance": material_performance,
                "supplier_quality": supplier_quality,
                "supplier_quality_trend": supplier_quality_trend,
                "material_traceability": material_traceability,
                "material_product_mapping": material_product_mapping,
                "defect_associated_lots": defect_associated_lots,
                "process_material_loss": process_material_loss,
                "management_answers": management_answers,
                "evidence_references": evidence,
                "analysis_limitations": list(dict.fromkeys(limitations)),
            }
        )
        if interpretation is not None:
            ai_interpretation = interpretation.model_dump(mode="json")

    total_material_count = len(
        {record.material for record in records if record.material}
    )
    result = {
        "schema_version": "1.0",
        "analysis_type": "material_intelligence",
        "executive_summary": (
            f"Đã phân tích {total_material_count} loại vật liệu, "
            f"{len(material_traceability)} mapping lô và "
            f"{len(supplier_quality)} nhà cung cấp."
        ),
        "ai_status": ai_status,
        "ai_error": ai_error,
        "data_quality": dataset.data_quality.model_dump(mode="json"),
        "data_structure": {
            "material_count": total_material_count,
            "material_type_count": len(
                {record.material_type for record in records if record.material_type}
            ),
            "material_lot_count": len(
                {record.material_lot for record in records if record.material_lot}
            ),
            "supplier_count": len(
                {record.supplier for record in records if record.supplier}
            ),
            "product_count": len(
                {record.product for record in records if record.product}
            ),
            "process_count": len(
                {record.process for record in records if record.process}
            ),
            "production_batch_count": len(
                {record.batch for record in records if record.batch}
            ),
        },
        "kpi_results": kpi_results,
        "material_performance": material_performance,
        "consumption_by_unit": sorted(
            [
                {
                    "unit": item["unit"],
                    "planned_consumption": _rounded(
                        sum(
                            record.planned_consumption or 0
                            for record in records
                            if record.material_unit == item["unit"]
                            and record.planned_consumption is not None
                        )
                    ),
                    "actual_consumption": _rounded(
                        sum(
                            record.actual_consumption or 0
                            for record in records
                            if record.material_unit == item["unit"]
                            and record.actual_consumption is not None
                        )
                    ),
                }
                for item in {row["unit"]: row for row in material_performance}.values()
                if item["unit"] is not None
            ],
            key=lambda item: item["unit"] or "",
        ),
        "process_material_loss": process_material_loss,
        "material_traceability": material_traceability,
        "material_product_mapping": material_product_mapping,
        "defect_associated_lots": defect_associated_lots,
        "supplier_quality": supplier_quality,
        "supplier_quality_trend": supplier_quality_trend,
        "management_answers": management_answers,
        "ai_interpretation": ai_interpretation,
        "evidence_references": evidence,
        "analysis_limitations": list(dict.fromkeys(limitations)),
    }
    return result
