from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models import (
    AIAuditLog,
    Alert,
    AlertUser,
    AnalysisHistory,
    GovernedReport,
    ImprovementCase,
    UserActionAudit,
)
from app.services.monitoring_auth import (
    accessible_plant_ids,
    get_current_user,
)


router = APIRouter(
    prefix="/api/enterprise-intelligence",
    tags=["Enterprise Intelligence"],
)

_ACTIVE_ALERT_STATUSES = ("open", "acknowledged", "investigating", "snoozed")
_ACTIVE_CASE_STATUSES = (
    "detected",
    "action_planned",
    "implemented",
    "measured",
    "evaluated",
)
_DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "key": "production",
        "label": "Production",
        "analysis_types": (
            "production_analysis",
            "production_intelligence",
            "production_insights",
        ),
    },
    {
        "key": "quality",
        "label": "Quality",
        "analysis_types": ("quality_analysis", "quality_intelligence"),
    },
    {
        "key": "machine",
        "label": "Machine",
        "analysis_types": ("machine_intelligence",),
    },
    {
        "key": "material",
        "label": "Material",
        "analysis_types": ("material_intelligence",),
    },
    {
        "key": "predictive",
        "label": "Predictive",
        "analysis_types": ("predictive_intelligence",),
    },
    {
        "key": "root_cause",
        "label": "Root Cause",
        "analysis_types": ("root_cause_intelligence",),
    },
)
_REPORT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "key": "daily_production",
        "title": "Daily Production Report",
        "cadence": "daily",
        "domains": ("production",),
    },
    {
        "key": "weekly_quality",
        "title": "Weekly Quality Report",
        "cadence": "weekly",
        "domains": ("quality",),
    },
    {
        "key": "monthly_performance",
        "title": "Monthly Performance Report",
        "cadence": "monthly",
        "domains": ("production", "quality", "machine"),
    },
    {
        "key": "machine_reliability",
        "title": "Machine Reliability Report",
        "cadence": "latest analysis",
        "domains": ("machine",),
    },
    {
        "key": "material_consumption",
        "title": "Material Consumption Report",
        "cadence": "latest analysis",
        "domains": ("material",),
    },
    {
        "key": "predictive_risk",
        "title": "Predictive Risk Report",
        "cadence": "latest analysis",
        "domains": ("predictive",),
    },
    {
        "key": "executive_manufacturing",
        "title": "Executive Manufacturing Report",
        "cadence": "latest analyses",
        "domains": tuple(item["key"] for item in _DOMAINS),
    },
)
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _candidate_action(item: Any) -> str | None:
    if isinstance(item, str):
        action = item.strip()
        return action or None
    record = _object(item)
    for key in ("action", "recommendation", "recommended_action", "text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _candidate_priority(item: Any, default: str = "medium") -> str:
    value = _object(item).get(
        "priority",
        _object(item).get("risk", _object(item).get("severity", default)),
    )
    normalized = str(value).strip().casefold()
    if normalized in ("urgent", "critical", "high"):
        return "high"
    if normalized in ("low", "info", "informational"):
        return "low"
    return "medium"


def _make_recommendation(
    *,
    domain: str,
    history: AnalysisHistory,
    action: str,
    priority: str,
    impact_basis: str,
    impact_rank: int,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    key = f"{domain}:{history.id}:{action.casefold()}"
    recommendation_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return {
        "id": recommendation_id,
        "domain": domain,
        "source_analysis_history_id": history.id,
        "source_analysis_type": history.analysis_type,
        "action": action,
        "priority": priority,
        "impact_basis": impact_basis,
        "impact_rank_within_domain": impact_rank,
        "evidence_ids": evidence_ids or [],
        "status": "suggested",
        "causality_claimed": False,
    }


def _recommendations_for_domain(
    domain: str,
    history: AnalysisHistory,
) -> list[dict[str, Any]]:
    result = _object(history.result_data)
    recommendations: list[dict[str, Any]] = []

    def append_item(
        item: Any,
        *,
        fallback_priority: str = "medium",
        impact_basis: str = "Khuyến nghị từ kết quả phân tích đã lưu.",
    ) -> None:
        action = _candidate_action(item)
        if action is None:
            return
        record = _object(item)
        evidence_ids = record.get("evidence_ids")
        if not isinstance(evidence_ids, list) and isinstance(
            record.get("evidence_id"), str
        ):
            evidence_ids = [record["evidence_id"]]
        recommendations.append(
            _make_recommendation(
                domain=domain,
                history=history,
                action=action,
                priority=_candidate_priority(item, fallback_priority),
                impact_basis=impact_basis,
                impact_rank=len(recommendations) + 1,
                evidence_ids=(
                    [str(value) for value in evidence_ids]
                    if isinstance(evidence_ids, list)
                    else []
                ),
            )
        )

    if domain in ("production", "quality"):
        for item in _list(result.get("recommendations")):
            append_item(item)
        bottlenecks = _object(result.get("bottleneck_analysis")).get("candidates")
        for item in _list(bottlenecks):
            candidate = _object(item)
            process = candidate.get("process") or candidate.get("entity")
            if process:
                append_item(
                    {
                        "action": f"Rà soát nút thắt tại công đoạn {process} và xác minh dữ liệu chờ.",
                        "priority": "medium",
                    },
                    impact_basis="Tín hiệu throughput/chờ trong phân tích sản xuất; không khẳng định nguyên nhân.",
                )
    elif domain == "machine":
        for item in _list(result.get("maintenance_priorities")):
            append_item(
                item,
                impact_basis=(
                    "Dựa trên số lỗi và downtime ngoài kế hoạch đã quan sát; "
                    "cần xác minh trước khi lập lệnh bảo trì."
                ),
            )
    elif domain == "material":
        for supplier in _list(result.get("supplier_quality")):
            record = _object(supplier)
            risk_level = record.get("risk_level")
            if risk_level not in {"high", "medium"}:
                continue
            name = record.get("supplier") or "chưa xác định"
            defect_rate = record.get("defect_rate_percent")
            append_item(
                {
                    "action": (
                        f"Rà soát chất lượng lô và xu hướng lỗi của nhà cung cấp {name}."
                    ),
                    "priority": risk_level,
                    "evidence_id": record.get("evidence_id"),
                },
                impact_basis=(
                    f"Supplier risk={risk_level}; defect rate={defect_rate}% "
                    "theo ngưỡng sàng lọc của Material Intelligence."
                ),
            )
        performances = _list(result.get("material_performance"))
        ranked = sorted(
            (
                item
                for item in performances
                if isinstance(item, dict)
                and isinstance(item.get("material_loss"), (int, float))
                and item["material_loss"] > 0
            ),
            key=lambda item: float(item["material_loss"]),
            reverse=True,
        )
        for index, item in enumerate(ranked[:5]):
            material = item.get("material") or "chưa xác định"
            unit = item.get("unit")
            loss = item.get("material_loss")
            waste_rate = item.get("waste_rate_percent")
            action = f"Rà soát hao hụt vật liệu {material} và đối chiếu lô/công đoạn."
            impact = f"Hao hụt quan sát: {loss:g}"
            if unit:
                impact += f" {unit}"
            if isinstance(waste_rate, (int, float)):
                impact += f"; waste rate {waste_rate:g}%"
            recommendations.append(
                _make_recommendation(
                    domain=domain,
                    history=history,
                    action=action,
                    priority="medium" if index == 0 else "low",
                    impact_basis=impact,
                    impact_rank=index + 1,
                )
            )
    elif domain == "predictive":
        for risk in _list(result.get("material_shortage_risk")):
            record = _object(risk)
            if record.get("status") == "shortage_signal":
                material = record.get("material") or "chưa xác định"
                quantity = record.get("shortage_quantity")
                append_item(
                    {
                        "action": f"Xác minh tồn kho và kế hoạch cấp vật liệu {material} trước kỳ dự báo.",
                        "priority": "high",
                    },
                    impact_basis=(
                        f"Tín hiệu thiếu hụt dự báo: {quantity:g} {record.get('unit') or ''}."
                        if isinstance(quantity, (int, float))
                        else "Dự báo tiêu hao vượt tồn kho đã cung cấp."
                    ),
                )
        for signal in _list(
            _object(result.get("quality_risk")).get("threshold_signals")
        ):
            record = _object(signal)
            if record.get("status") == "threshold_exceedance_signal":
                append_item(
                    {
                        "action": (
                            "Kiểm tra quy trình và mẫu kiểm định tại kỳ dự báo "
                            f"{record.get('period', 'sắp tới')}."
                        ),
                        "priority": "high",
                    },
                    impact_basis=(
                        f"Tỷ lệ lỗi dự báo {record.get('value')}% vượt ngưỡng "
                        f"{record.get('threshold_percent')}%; đây không phải xác suất."
                    ),
                )
        for signal in _list(
            _object(result.get("production_plan_completion")).get("daily_assessment")
        ):
            record = _object(signal)
            if record.get("status") == "plan_shortfall_signal":
                append_item(
                    {
                        "action": (
                            "Rà soát kế hoạch và năng lực cho ngày "
                            f"{record.get('period', 'dự báo')}."
                        ),
                        "priority": "medium",
                    },
                    impact_basis=(
                        f"Tỷ lệ đạt kế hoạch dự báo {record.get('value')}%; "
                        "không phải cam kết hoàn thành."
                    ),
                )
    elif domain == "root_cause":
        actions = _list(result.get("corrective_actions"))
        if not actions:
            actions = _list(result.get("root_cause_candidates"))
        for item in actions:
            append_item(
                item,
                impact_basis=(
                    "Giả thuyết/hành động từ phân tích nguyên nhân; "
                    "cần xác minh và theo dõi KPI trước/sau."
                ),
            )

    unique: dict[str, dict[str, Any]] = {}
    for recommendation in recommendations:
        unique.setdefault(recommendation["id"], recommendation)
    return list(unique.values())


def _domain_summary(
    history: AnalysisHistory | None,
    domain: dict[str, Any],
    *,
    since: datetime,
) -> dict[str, Any]:
    if history is None:
        return {
            "key": domain["key"],
            "label": domain["label"],
            "status": "no_analysis",
            "latest_analysis": None,
            "recommendations": [],
            "is_stale": False,
        }
    result = _object(history.result_data)
    limitations = _list(result.get("analysis_limitations"))
    quality = _object(result.get("data_quality"))
    quality_score = quality.get("quality_score")
    is_stale = (_as_utc(history.created_at) or since) < since
    recommendation_items = (
        []
        if is_stale
        else _recommendations_for_domain(domain["key"], history)
    )
    return {
        "key": domain["key"],
        "label": domain["label"],
        "status": (
            "stale"
            if is_stale
            else "limited"
            if limitations
            else "available"
        ),
        "latest_analysis": {
            "id": history.id,
            "analysis_type": history.analysis_type,
            "title": history.title,
            "summary": history.summary
            or result.get("executive_summary")
            or result.get("problem_statement"),
            "source_filename": history.source_filename,
            "created_at": _iso(history.created_at),
            "algorithm_version": history.algorithm_version,
            "ai_status": history.ai_status,
            "source_file_sha256": history.source_file_sha256,
            "evidence_count": len(
                history.evidence or _list(result.get("evidence_references"))
            ),
            "quality_score": (
                quality_score
                if isinstance(quality_score, (int, float))
                else None
            ),
            "limitations": [str(item) for item in limitations[:8]],
        },
        "recommendations": recommendation_items,
        "is_stale": is_stale,
    }


def _scoped_plant_clause(query, user: AlertUser, db: Session, column):
    plant_ids = accessible_plant_ids(db, user)
    if plant_ids is None:
        return query, plant_ids
    if not plant_ids:
        return query.where(False), plant_ids
    return query.where(column.in_(plant_ids)), plant_ids


def _report_readiness(domains: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    templates = []
    for template in _REPORT_TEMPLATES:
        sources = [
            domains[key]["latest_analysis"]
            for key in template["domains"]
            if domains[key]["latest_analysis"] is not None
            and not domains[key]["is_stale"]
        ]
        missing = [
            f"{domains[key]['label']} (stale)"
            if domains[key]["is_stale"]
            else domains[key]["label"]
            for key in template["domains"]
            if domains[key]["latest_analysis"] is None or domains[key]["is_stale"]
        ]
        templates.append(
            {
                "key": template["key"],
                "title": template["title"],
                "cadence": template["cadence"],
                "status": (
                    "ready"
                    if not missing
                    else "partial"
                    if sources
                    else "no_source"
                ),
                "required_domains": list(template["domains"]),
                "missing_domains": missing,
                "source_analysis_ids": [source["id"] for source in sources],
                "source_summaries": [
                    {
                        "domain": domains[key]["label"],
                        "summary": domains[key]["latest_analysis"]["summary"],
                        "analysis_history_id": domains[key]["latest_analysis"]["id"],
                    }
                    for key in template["domains"]
                    if domains[key]["latest_analysis"] is not None
                    and not domains[key]["is_stale"]
                ],
                "governance": "Create, review, approve, and export through Governed Reports.",
            }
        )
    return templates


@router.get("/overview")
def enterprise_overview(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: AlertUser = Depends(get_current_user),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    latest_histories: dict[str, AnalysisHistory | None] = {}
    for domain in _DOMAINS:
        query = select(AnalysisHistory).where(
            AnalysisHistory.analysis_type.in_(domain["analysis_types"])
        )
        if user.role != "admin":
            query = query.where(AnalysisHistory.creator_user_id == user.id)
        latest_histories[domain["key"]] = db.scalar(
            query.order_by(
                AnalysisHistory.created_at.desc(),
                AnalysisHistory.id.desc(),
            ).limit(1)
        )

    domains = {
        domain["key"]: _domain_summary(
            latest_histories[domain["key"]],
            domain,
            since=since,
        )
        for domain in _DOMAINS
    }
    recommendations = [
        recommendation
        for domain in domains.values()
        for recommendation in domain["recommendations"]
    ]
    recommendations.sort(
        key=lambda item: (
            _PRIORITY_ORDER.get(item["priority"], 3),
            item["impact_rank_within_domain"],
            item["domain"],
            item["id"],
        )
    )
    for rank, recommendation in enumerate(recommendations, start=1):
        recommendation["rank"] = rank

    active_alert_query, plant_ids = _scoped_plant_clause(
        select(Alert).where(Alert.status.in_(_ACTIVE_ALERT_STATUSES)),
        user,
        db,
        Alert.plant_id,
    )
    active_alerts = db.scalars(
        active_alert_query.order_by(
            Alert.detected_at.desc(),
            Alert.id.desc(),
        ).limit(100)
    ).all()
    severity_counts_query, _ = _scoped_plant_clause(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.detected_at >= since)
        .group_by(Alert.severity),
        user,
        db,
        Alert.plant_id,
    )
    alert_severity_counts = {
        severity: count
        for severity, count in db.execute(severity_counts_query).all()
    }
    total_active_alerts_query, _ = _scoped_plant_clause(
        select(func.count(Alert.id)).where(
            Alert.status.in_(_ACTIVE_ALERT_STATUSES)
        ),
        user,
        db,
        Alert.plant_id,
    )
    total_active_alerts = db.scalar(total_active_alerts_query) or 0

    case_query = select(ImprovementCase).where(
        ImprovementCase.status.in_(_ACTIVE_CASE_STATUSES)
    )
    if plant_ids is not None:
        case_query = (
            case_query.where(ImprovementCase.plant_id.in_(plant_ids))
            if plant_ids
            else case_query.where(False)
        )
    active_case_count = db.scalar(
        select(func.count()).select_from(case_query.subquery())
    ) or 0
    overdue_case_query = select(func.count(ImprovementCase.id)).where(
        ImprovementCase.status.in_(_ACTIVE_CASE_STATUSES),
        ImprovementCase.action_due_at.is_not(None),
        ImprovementCase.action_due_at < now,
    )
    if plant_ids is not None:
        overdue_case_query = (
            overdue_case_query.where(ImprovementCase.plant_id.in_(plant_ids))
            if plant_ids
            else overdue_case_query.where(False)
        )
    overdue_case_count = db.scalar(overdue_case_query) or 0
    cases = db.scalars(
        case_query.order_by(
            ImprovementCase.action_due_at.is_(None),
            ImprovementCase.action_due_at,
            ImprovementCase.updated_at.desc(),
        ).limit(100)
    ).all()
    overdue_cases = []
    case_items = []
    for case in cases:
        due_at = _as_utc(case.action_due_at)
        is_overdue = due_at is not None and due_at < now
        item = {
            "id": case.id,
            "plant_id": case.plant_id,
            "source_alert_id": case.source_alert_id,
            "problem": case.problem,
            "action": case.action,
            "priority": case.action_priority,
            "status": case.status,
            "owner_user_id": case.owner_user_id,
            "action_due_at": due_at.isoformat() if due_at else None,
            "overdue": is_overdue,
            "escalation_status": (
                "overdue_escalation_required" if is_overdue else "on_track"
            ),
            "updated_at": _iso(case.updated_at),
        }
        case_items.append(item)
        if is_overdue:
            overdue_cases.append(item)

    audit_query = select(UserActionAudit)
    if user.role != "admin":
        audit_query = audit_query.where(UserActionAudit.actor_user_id == user.id)
    audit_items = db.scalars(
        audit_query.order_by(
            UserActionAudit.created_at.desc(),
            UserActionAudit.id.desc(),
        ).limit(20)
    ).all()
    audit_trail = [
        {
            "id": item.id,
            "actor_user_id": item.actor_user_id,
            "action": item.action,
            "object_type": item.object_type,
            "object_id": item.object_id,
            "outcome": item.outcome,
            "created_at": _iso(item.created_at),
        }
        for item in audit_items
    ]

    if user.role == "admin":
        ai_audit_rows = db.execute(
            select(
                AIAuditLog.analysis_type,
                AIAuditLog.status,
                func.count(AIAuditLog.id),
            )
            .where(AIAuditLog.created_at >= since)
            .group_by(AIAuditLog.analysis_type, AIAuditLog.status)
        ).all()
        ai_audit = {
            "status": "available",
            "period_days": days,
            "by_analysis_and_status": [
                {
                    "analysis_type": analysis_type,
                    "status": status,
                    "count": count,
                }
                for analysis_type, status, count in ai_audit_rows
            ],
            "sensitive_prompts_and_responses_exposed": False,
        }
    else:
        ai_audit = {
            "status": "admin_only",
            "sensitive_prompts_and_responses_exposed": False,
        }

    report_query, _ = _scoped_plant_clause(
        select(GovernedReport),
        user,
        db,
        GovernedReport.plant_id,
    )
    governed_report_count = db.scalar(
        select(func.count()).select_from(report_query.subquery())
    ) or 0
    latest_governed_reports = db.scalars(
        report_query.order_by(
            GovernedReport.created_at.desc(),
            GovernedReport.id.desc(),
        ).limit(5)
    ).all()

    analyzed_domains = sum(
        domain["latest_analysis"] is not None and not domain["is_stale"]
        for domain in domains.values()
    )
    return {
        "generated_at": now.isoformat(),
        "period_days": days,
        "enterprise_overview": {
            "domain_count": len(_DOMAINS),
            "analyzed_domain_count": analyzed_domains,
            "analysis_coverage_percent": round(
                analyzed_domains / len(_DOMAINS) * 100,
                2,
            ),
            "active_alert_count": total_active_alerts,
            "alerts_by_severity_in_period": alert_severity_counts,
            "active_work_item_count": active_case_count,
            "overdue_work_item_count": overdue_case_count,
            "ranked_recommendation_count": len(recommendations),
        },
        "domains": list(domains.values()),
        "recommendations": recommendations[:100],
        "intelligent_alerts": [
            {
                "id": alert.id,
                "severity": alert.severity,
                "status": alert.status,
                "title": alert.title,
                "summary": alert.summary,
                "metric": alert.metric,
                "plant_id": alert.plant_id,
                "line_id": alert.line_id,
                "assigned_to": alert.assigned_to,
                "recommended_check": alert.recommended_check,
                "detected_at": _iso(alert.detected_at),
            }
            for alert in active_alerts
        ],
        "workflow": {
            "active_cases": case_items,
            "overdue_cases": overdue_cases,
            "escalation_policy": (
                "Overdue work is flagged for escalation in this overview; "
                "notification/escalation routing remains a manager action."
            ),
        },
        "audit_governance": {
            "user_activity": audit_trail,
            "ai_analysis_audit": ai_audit,
            "traceability": [
                {
                    "domain": domain["key"],
                    "analysis_history_id": domain["latest_analysis"]["id"],
                    "analysis_type": domain["latest_analysis"]["analysis_type"],
                    "algorithm_version": domain["latest_analysis"]["algorithm_version"],
                    "ai_status": domain["latest_analysis"]["ai_status"],
                    "source_file_sha256": domain["latest_analysis"]["source_file_sha256"],
                    "evidence_count": domain["latest_analysis"]["evidence_count"],
                }
                for domain in domains.values()
                if domain["latest_analysis"] is not None
            ],
        },
        "reporting": {
            "governed_report_count": governed_report_count,
            "latest_governed_reports": [
                {
                    "id": report.id,
                    "title": report.title,
                    "current_version": report.current_version,
                    "created_at": _iso(report.created_at),
                }
                for report in latest_governed_reports
            ],
            "report_templates": _report_readiness(domains),
            "workflow": "Use /api/reports to save, review, approve, publish, and export source-linked reports.",
        },
    }
