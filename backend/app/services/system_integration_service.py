from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.intelligence_orchestrator import (
    orchestrate_manufacturing_analysis,
)
from app.services.machine_intelligence_service import (
    analyze_machine_intelligence,
)
from app.services.material_intelligence_service import (
    analyze_material_intelligence,
)
from app.services.predictive_intelligence_service import (
    analyze_predictive_intelligence,
)
from app.services.production_intelligence_service import (
    analyze_production_intelligence,
)
from app.services.quality_intelligence_service import (
    analyze_quality_intelligence,
)
from app.services.root_cause_intelligence_service import (
    analyze_root_cause_intelligence,
)


_DOMAIN_LABELS = {
    "production": "Production Intelligence",
    "quality": "Quality Intelligence",
    "root_cause": "Root Cause Intelligence",
    "machine": "Machine Intelligence",
    "material": "Material Intelligence",
    "predictive": "Predictive Intelligence",
}
_METRIC_DOMAINS = {
    "actual_production": "production",
    "planned_production": "production",
    "production_achievement_rate": "production",
    "defect_rate": "quality",
    "defective_units": "quality",
    "defects": "quality",
    "first_pass_yield": "quality",
    "downtime_minutes": "machine",
    "cycle_time_seconds": "machine",
    "waiting_time_minutes": "machine",
    "operating_time_minutes": "machine",
    "available_time_minutes": "machine",
}


def _result_status(result: dict[str, Any]) -> str:
    limitations = result.get("analysis_limitations")
    data_quality = result.get("data_quality")
    if isinstance(data_quality, dict) and data_quality.get(
        "rows_used_for_analysis"
    ) == 0:
        return "insufficient_data"
    foundation = result.get("predictive_data_foundation")
    if (
        isinstance(foundation, dict)
        and foundation.get("status") == "insufficient_data"
    ):
        return "insufficient_data"
    if isinstance(limitations, list) and limitations:
        return "limited"
    confidence = result.get("confidence_assessment")
    if isinstance(confidence, dict) and confidence.get("level") == "limited":
        return "limited"
    return "available"


def _domain_summary(key: str, result: dict[str, Any]) -> dict[str, Any]:
    evidence = result.get("evidence_references")
    limitations = result.get("analysis_limitations")
    summary = (
        result.get("executive_summary")
        or result.get("summary")
        or result.get("problem_statement")
    )
    return {
        "key": key,
        "label": _DOMAIN_LABELS[key],
        "analysis_type": result.get("analysis_type", f"{key}_intelligence"),
        "status": _result_status(result),
        "summary": summary if isinstance(summary, str) else None,
        "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
        "limitations": (
            [str(value) for value in limitations[:10]]
            if isinstance(limitations, list)
            else []
        ),
        "result": result,
    }


def _unique_evidence(
    results: dict[str, dict[str, Any]],
    unified_analysis: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    entries: list[dict[str, Any]] = []
    id_map: dict[tuple[str, str], str] = {}
    sources = {
        **results,
        "analytics_engine": unified_analysis,
    }
    for domain, result in sources.items():
        references = result.get("evidence_references")
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict) or not isinstance(
                reference.get("id"), str
            ):
                continue
            source_id = reference["id"]
            evidence_id = f"{domain}:{source_id}"
            id_map[(domain, source_id)] = evidence_id
            entries.append(
                {
                    **reference,
                    "id": evidence_id,
                    "source_domain": domain,
                    "source_evidence_id": source_id,
                }
            )
    return entries, id_map


def _cross_findings(
    results: dict[str, dict[str, Any]],
    evidence_id_map: dict[tuple[str, str], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    integrated_evidence: list[dict[str, Any]] = []
    root_cause = results["root_cause"]
    for correlation in root_cause.get("notable_correlations", []):
        if not isinstance(correlation, dict):
            continue
        left = correlation.get("left_variable")
        right = correlation.get("right_variable")
        left_domain = _METRIC_DOMAINS.get(left)
        right_domain = _METRIC_DOMAINS.get(right)
        sample_count = correlation.get("sample_count")
        coefficient = correlation.get("spearman")
        if (
            left_domain is None
            or right_domain is None
            or left_domain == right_domain
            or not isinstance(sample_count, int)
            or sample_count < 8
            or not isinstance(coefficient, (int, float))
            or abs(coefficient) < 0.6
        ):
            continue
        source_ids = correlation.get("evidence_ids")
        findings.append(
            {
                "type": "cross_domain_numeric_association",
                "domains": [left_domain, right_domain],
                "variables": [left, right],
                "spearman_correlation": coefficient,
                "sample_count": sample_count,
                "evidence_ids": [
                    evidence_id_map[("root_cause", source_id)]
                    for source_id in source_ids or []
                    if ("root_cause", source_id) in evidence_id_map
                ],
                "interpretation": (
                    "Liên hệ thống kê trên các quan sát đã ghép; không chứng minh "
                    "quan hệ nhân quả."
                ),
                "status": "association_only",
            }
        )

    material = results["material"]
    defect_lots = material.get("defect_associated_lots")
    if isinstance(defect_lots, list):
        for lot in defect_lots[:20]:
            if not isinstance(lot, dict) or not lot.get("evidence_id"):
                continue
            supplier = lot.get("supplier")
            product = lot.get("product")
            findings.append(
                {
                    "type": "material_lot_quality_association",
                    "domains": ["material", "quality"],
                    "material_lot": lot.get("material_lot"),
                    "supplier": supplier,
                    "product": product,
                    "defect_rate_percent": lot.get("defect_rate_percent"),
                    "record_count": lot.get("record_count"),
                    "evidence_ids": [
                        evidence_id_map[
                            ("material", str(lot["evidence_id"]))
                        ]
                    ]
                    if (
                        "material",
                        str(lot["evidence_id"]),
                    )
                    in evidence_id_map
                    else [],
                    "interpretation": (
                        "Lô có bản ghi lỗi được liên kết; đây là quan sát truy xuất, "
                        "không xác nhận lô gây ra lỗi."
                    ),
                    "status": "traceability_signal",
                }
            )

    predictive = results["predictive"]
    failure_assessment = predictive.get("machine_failure_assessment")
    production_forecast = (
        predictive.get("forecasts", {}).get("production")
        if isinstance(predictive.get("forecasts"), dict)
        else None
    )
    if (
        isinstance(failure_assessment, dict)
        and failure_assessment.get("status") == "empirical_frequency_available"
        and isinstance(production_forecast, dict)
        and production_forecast.get("status") == "forecasted"
    ):
        forecast_rows = production_forecast.get("forecast")
        forecast_values = [
            row.get("value")
            for row in forecast_rows
            if isinstance(row, dict)
            and isinstance(row.get("value"), (int, float))
        ] if isinstance(forecast_rows, list) else []
        if forecast_values:
            forecast_total = sum(forecast_values)
            for assessment in failure_assessment.get("by_machine", []):
                if (
                    not isinstance(assessment, dict)
                    or assessment.get("status")
                    != "empirical_frequency_available"
                ):
                    continue
                machine_name = assessment.get("machine")
                if not machine_name:
                    continue
                failure_evidence_id = f"X{len(integrated_evidence) + 1:04d}"
                integrated_evidence.append(
                    {
                        "id": failure_evidence_id,
                        "metric": "machine_failure_day_frequency",
                        "value": assessment.get(
                            "empirical_failure_probability_percent"
                        ),
                        "unit": "percent",
                        "method": "historical_failure_days_divided_by_observed_days",
                        "record_count": assessment.get("observed_period_count"),
                        "source_domain": "predictive",
                        "source_analysis_type": "machine_failure_assessment",
                        "dimensions": {"machine": str(machine_name)},
                    }
                )
                forecast_evidence_id = f"X{len(integrated_evidence) + 1:04d}"
                integrated_evidence.append(
                    {
                        "id": forecast_evidence_id,
                        "metric": "forecast_production_total",
                        "value": forecast_total,
                        "unit": "production_unit",
                        "method": "sum_of_statistical_production_forecast_periods",
                        "record_count": len(forecast_values),
                        "source_domain": "predictive",
                        "source_analysis_type": "production_forecast",
                    }
                )
                findings.append(
                    {
                        "type": "machine_predictive_production_review",
                        "domains": ["machine", "predictive", "production"],
                        "machine": machine_name,
                        "forecast_horizon_periods": len(forecast_values),
                        "historical_failure_day_frequency_percent": assessment.get(
                            "empirical_failure_probability_percent"
                        ),
                        "forecast_production_total": forecast_total,
                        "evidence_ids": [
                            failure_evidence_id,
                            forecast_evidence_id,
                        ],
                        "interpretation": (
                            "Tần suất ngày lỗi của máy và dự báo sản lượng cùng khả "
                            "dụng; cần đối chiếu máy/dây chuyền với kế hoạch trước "
                            "khi ưu tiên bảo trì. Không khẳng định lỗi máy sẽ gây "
                            "thiếu sản lượng."
                        ),
                        "status": "joint_review_signal",
                    }
                )
    return findings, integrated_evidence


def analyze_system_integration(
    dataframe: pd.DataFrame,
    *,
    problem_statement: str,
    column_mapping: dict[str, str] | None = None,
    source_timezone: str = "UTC",
    downtime_unit: str = "minutes",
    cycle_time_unit: str = "seconds",
    duration_unit: str = "minutes",
    production_unit: str = "unit",
    measurement_unit: str = "unit",
    horizon: int = 7,
    include_ai: bool = False,
) -> dict[str, Any]:
    unified_analysis = orchestrate_manufacturing_analysis(
        dataframe,
        column_mapping=column_mapping,
        source_timezone=source_timezone,
        downtime_unit=downtime_unit,
        production_unit=production_unit,
        include_ai=False,
    )
    results = {
        "production": analyze_production_intelligence(
            dataframe,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            cycle_time_unit=cycle_time_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            include_ai=False,
        ),
        "quality": analyze_quality_intelligence(
            dataframe,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            production_unit=production_unit,
            measurement_unit=measurement_unit,
            include_ai=False,
        ),
        "root_cause": analyze_root_cause_intelligence(
            dataframe,
            problem_statement=problem_statement,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            cycle_time_unit=cycle_time_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            include_ai=include_ai,
        ),
        "machine": analyze_machine_intelligence(
            dataframe,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            duration_unit=duration_unit,
            cycle_time_unit=cycle_time_unit,
        ),
        "material": analyze_material_intelligence(
            dataframe,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            production_unit=production_unit,
            include_ai=False,
        ),
        "predictive": analyze_predictive_intelligence(
            dataframe,
            column_mapping=column_mapping,
            source_timezone=source_timezone,
            downtime_unit=downtime_unit,
            duration_unit=duration_unit,
            production_unit=production_unit,
            cycle_time_unit=cycle_time_unit,
            horizon=horizon,
        ),
    }
    domain_summaries = {
        key: _domain_summary(key, result) for key, result in results.items()
    }
    evidence_references, evidence_id_map = _unique_evidence(
        results,
        unified_analysis,
    )
    cross_findings, integrated_evidence = _cross_findings(
        results,
        evidence_id_map,
    )
    evidence_references.extend(integrated_evidence)
    has_numeric_association = any(
        finding["type"] == "cross_domain_numeric_association"
        for finding in cross_findings
    )
    limitations = list(
        dict.fromkeys(
            limitation
            for result in results.values()
            for limitation in result.get("analysis_limitations", [])
            if isinstance(limitation, str)
        )
    )
    return {
        "schema_version": "1.0",
        "analysis_type": "system_integration",
        "analysis_contract": {
            "input_schema_version": unified_analysis["schema_version"],
            "domain_order": list(_DOMAIN_LABELS),
            "cross_findings_are_causal": False,
            "evidence_ids_are_domain_scoped": True,
            "ai_scope": "Root Cause cross-domain interpretation only.",
        },
        "problem_statement": problem_statement,
        "data_quality": unified_analysis["data_contract"]["data_quality"],
        "unified_analysis": unified_analysis,
        "domains": domain_summaries,
        "cross_intelligence": {
            "status": (
                "signals_found"
                if has_numeric_association
                else "traceability_only"
                if cross_findings
                else "limited_data"
            ),
            "findings": cross_findings,
            "analysis_limitations": limitations,
            "minimum_correlation_sample_count": 8,
            "minimum_absolute_spearman_correlation": 0.6,
        },
        "ai_orchestration": {
            "status": results["root_cause"]["ai_status"],
            "scope": "root_cause_cross_domain_interpretation",
        },
        "evidence_references": evidence_references,
        "analysis_limitations": limitations,
    }
