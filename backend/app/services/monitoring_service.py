import hashlib
import http.client
import ipaddress
import json
import logging
import math
import smtplib
import socket
import ssl
import statistics
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.models.monitoring import (
    Alert,
    AlertDelivery,
    AlertNotification,
    AlertRule,
    AlertStatusHistory,
    AlertUser,
    AlertUserPlant,
    MonitoringDataset,
)
from app.services.hardware_service import generate_custom_hardware_response
from app.services.hardware_trend_service import analyze_hardware_trends
from app.services.multi_file_service import _parse_numeric
from app.services.monitoring_lock import postgres_advisory_lock


logger = logging.getLogger(__name__)
METRICS = ("production", "defects", "defect_rate", "downtime")
METRIC_LABELS = {
    "production": "sản lượng",
    "defects": "số lỗi",
    "defect_rate": "tỷ lệ lỗi",
    "downtime": "thời gian dừng",
}
SCHEDULE_SECONDS = {"hours": 3600, "days": 86400, "weeks": 604800}
PANDAS_PERIOD_FREQUENCY = {"day": "D", "week": "W", "month": "M"}
MAX_DELIVERY_ATTEMPTS = 5
DATASET_EVALUATION_LOCK_KEY = 0x4D4F4E49544F5202
ALERT_DELIVERY_LOCK_KEY = 0x4D4F4E49544F5203
SNOOZE_EXPIRY_LOCK_KEY = 0x4D4F4E49544F5204
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def notification_channel_status() -> dict[str, bool]:
    webhook = urlsplit(settings.ALERT_WEBHOOK_URL or "")
    return {
        "in_app": True,
        "email": bool(
            settings.ALERT_SMTP_HOST
            and settings.ALERT_SMTP_FROM
            and (settings.ALERT_SMTP_USE_TLS or settings.ALERT_SMTP_PORT == 465)
        ),
        "webhook": webhook.scheme == "https" and bool(webhook.hostname),
    }


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _product_value(record: dict[str, Any]) -> str:
    for column in ("Product", "Model"):
        value = record.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _series_for_records(
    records: list[dict[str, Any]], granularity: str
) -> list[dict[str, Any]]:
    if not records:
        return []
    frame = pd.DataFrame.from_records(records)
    if "Date" not in frame.columns or "Production" not in frame.columns:
        return []
    result = analyze_hardware_trends(frame, granularity=granularity)
    return result["time_series"]


def _iqr_bounds(values: list[float]) -> tuple[float, float]:
    quartiles = statistics.quantiles(values, n=4, method="inclusive")
    spread = quartiles[2] - quartiles[0]
    return quartiles[0] - 1.5 * spread, quartiles[2] + 1.5 * spread


def _anomaly_candidate(
    series: list[dict[str, Any]],
    metric: str,
    scope_type: str,
    scope_value: str | None,
) -> dict[str, Any] | None:
    valid = [
        point
        for point in series
        if _safe_float(point.get(metric)) is not None
    ]
    if len(valid) < 9:
        return None
    history = [_safe_float(point[metric]) for point in valid[:-1]]
    current = _safe_float(valid[-1][metric])
    if current is None or any(value is None for value in history):
        return None
    numeric_history = [value for value in history if value is not None]
    lower, upper = _iqr_bounds(numeric_history)
    mean = statistics.mean(numeric_history)
    deviation = statistics.pstdev(numeric_history)
    z_score = (current - mean) / deviation if deviation else 0.0
    outside_iqr = current < lower or current > upper
    outside_z = abs(z_score) >= 3
    if not outside_iqr and not outside_z:
        return None
    return {
        "metric": metric,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "period": valid[-1]["period_start"],
        "observed": current,
        "baseline": numeric_history,
        "lower": round(lower, 4),
        "upper": round(upper, 4),
        "mean": round(mean, 4),
        "z_score": round(z_score, 3),
        "iqr_triggered": outside_iqr,
        "z_score_triggered": outside_z,
        "production": _safe_float(valid[-1].get("production")) or 0,
        "defects": _safe_float(valid[-1].get("defects")) or 0,
    }


def _statistical_candidates(
    records: list[dict[str, Any]], granularity: str
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    dimensions = ("", "Product", "Model", "Supplier")
    for dimension in dimensions:
        groups: dict[str | None, list[dict[str, Any]]] = {}
        if dimension:
            for record in records:
                raw = record.get(dimension)
                key = str(raw).strip() if raw is not None and str(raw).strip() else "Không xác định"
                groups.setdefault(key, []).append(record)
        else:
            groups[None] = records
        scope_type = dimension.lower() if dimension else "plant"
        for scope_value, grouped_records in groups.items():
            try:
                series = _series_for_records(grouped_records, granularity)
            except ValueError:
                continue
            for metric in ("defect_rate", "defects", "downtime", "production"):
                candidate = _anomaly_candidate(
                    series, metric, scope_type, scope_value
                )
                if candidate is not None:
                    candidates.append(candidate)

    frame = pd.DataFrame.from_records(records)
    if {"Batch", "Date", "Production", "Defects"}.issubset(frame.columns):
        frame["__date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame["__production"] = frame["Production"].map(
            lambda value: _safe_float(_parse_numeric(value)[0])
        )
        frame["__defects"] = frame["Defects"].map(
            lambda value: _safe_float(_parse_numeric(value)[0])
        )
        frame["__batch"] = frame["Batch"].map(
            lambda value: str(value).strip()
            if value is not None and not pd.isna(value)
            else ""
        )
        frame = frame.dropna(subset=["__date", "__production", "__defects"])
        frame = frame[frame["__batch"] != ""]
        if not frame.empty:
            frequency = PANDAS_PERIOD_FREQUENCY[granularity]
            latest_period = frame["__date"].max().to_period(frequency)
            batch_values: list[tuple[str, float, float, float]] = []
            for batch, batch_frame in frame[
                frame["__date"].dt.to_period(frequency) == latest_period
            ].groupby("__batch"):
                production = float(batch_frame["__production"].sum())
                defects = float(batch_frame["__defects"].sum())
                if production > 0 and str(batch).strip():
                    batch_values.append((str(batch), defects / production * 100, production, defects))
            if len(batch_values) >= 9:
                rates = [item[1] for item in batch_values]
                lower, upper = _iqr_bounds(rates)
                mean = statistics.mean(rates)
                deviation = statistics.pstdev(rates)
                for batch, rate, production, defects in batch_values:
                    z_score = (rate - mean) / deviation if deviation else 0.0
                    iqr_triggered = rate < lower or rate > upper
                    z_triggered = abs(z_score) >= 3
                    if not iqr_triggered and not z_triggered:
                        continue
                    candidates.append(
                        {
                            "metric": "defect_rate",
                            "scope_type": "batch",
                            "scope_value": batch,
                            "period": str(latest_period.start_time.date()),
                            "observed": round(rate, 2),
                            "baseline": [
                                round(other_rate, 2)
                                for other_batch, other_rate, _, _ in batch_values
                                if other_batch != batch
                            ],
                            "baseline_type": "batches",
                            "lower": round(lower, 4),
                            "upper": round(upper, 4),
                            "mean": round(mean, 4),
                            "z_score": round(z_score, 3),
                            "iqr_triggered": iqr_triggered,
                            "z_score_triggered": z_triggered,
                            "production": production,
                            "defects": defects,
                        }
                    )
    return candidates


def _fingerprint(parts: list[Any]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_alert_event(
    db: Session,
    alert: Alert,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    actor_user_id: int | None,
    note: str | None = None,
    event_data: dict[str, Any] | None = None,
) -> AlertStatusHistory:
    event = AlertStatusHistory(
        alert_id=alert.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        note=note,
        event_data=event_data,
    )
    db.add(event)
    db.flush()
    event_key = f"event:{event.id}"
    if event_type in ("severity_escalated", "severity_changed") and event_data:
        message = (
            f"Cảnh báo #{alert.id}: mức độ "
            f"{event_data.get('previous_severity', '-')}"
            f" → {event_data.get('severity', '-')}"
        )
    else:
        message = (
            f"Cảnh báo #{alert.id}: {event_type} "
            f"({from_status or '-'} → {to_status or '-'})"
        )
    recipients = db.scalars(
        select(AlertUser).where(
            AlertUser.is_active.is_(True),
            (
                (AlertUser.role == "admin")
                | AlertUser.id.in_(
                    select(AlertUserPlant.user_id).where(
                        AlertUserPlant.plant_id == alert.plant_id
                    )
                )
            ),
        )
    ).all()
    payload = {
        "event_id": event.id,
        "event_type": event_type,
        "from_status": from_status,
        "to_status": to_status,
        "note": note,
        "event_data": event_data or {},
        "alert_id": alert.id,
        "title": alert.title,
        "summary": alert.summary,
        "severity": alert.severity,
        "metric": alert.metric,
        "scope_type": alert.scope_type,
        "scope_value": alert.scope_value,
        "observed_value": alert.observed_value,
        "threshold_value": alert.threshold_value,
    }
    for user in recipients:
        db.add(
            AlertNotification(
                user_id=user.id,
                alert_id=alert.id,
                event_key=event_key,
                message=message[:300],
            )
        )
        if user.email_notifications and notification_channel_status()["email"]:
            db.add(
                AlertDelivery(
                    delivery_key=f"alert:{alert.id}:{event_key}:email:{user.id}",
                    alert_id=alert.id,
                    channel="email",
                    payload=payload,
                    recipient_user_id=user.id,
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )
    if notification_channel_status()["webhook"]:
        db.add(
            AlertDelivery(
                delivery_key=f"alert:{alert.id}:{event_key}:webhook",
                alert_id=alert.id,
                channel="webhook",
                payload=payload,
                status="pending",
                attempt_count=0,
                next_attempt_at=datetime.now(timezone.utc),
            )
        )
    return event


def _emit_alert(
    db: Session,
    *,
    plant_id: int,
    line_id: int | None,
    alert_type: str,
    rule_id: int | None,
    metric: str,
    scope_type: str,
    scope_value: str | None,
    severity: str,
    title: str,
    summary: str,
    observed: float,
    threshold: float | None,
    period: str,
    production: float,
    defects: float,
    evidence: list[dict[str, Any]],
    recommended_check: str,
    confidence: str,
) -> Alert:
    key = _fingerprint(
        [plant_id, line_id, alert_type, rule_id, metric, scope_type, scope_value, period]
    )
    alert = db.scalar(select(Alert).where(Alert.dedupe_key == key))
    now = datetime.now(timezone.utc)
    if alert is not None:
        alert.last_seen_at = now
        alert.observed_value = observed
        alert.summary = summary
        alert.impact = {"production": production, "defects": defects}
        alert.evidence = evidence
        previous_severity = alert.severity
        alert.severity = severity
        alert.confidence = confidence
        if previous_severity != severity:
            record_alert_event(
                db,
                alert,
                event_type=(
                    "severity_escalated"
                    if SEVERITY_RANK[severity] > SEVERITY_RANK[previous_severity]
                    else "severity_changed"
                ),
                from_status=alert.status,
                to_status=alert.status,
                actor_user_id=None,
                note="Mức độ cảnh báo được cập nhật theo dữ liệu mới.",
                event_data={
                    "previous_severity": previous_severity,
                    "severity": severity,
                    "period": period,
                },
            )
        if alert.status in ("resolved", "closed"):
            previous_status = alert.status
            alert.status = "open"
            alert.snoozed_until = None
            record_alert_event(
                db,
                alert,
                event_type="reopened",
                from_status=previous_status,
                to_status="open",
                actor_user_id=None,
                note="Dấu hiệu cảnh báo được phát hiện lại từ dữ liệu mới.",
                event_data={
                    "observed_value": observed,
                    "period": period,
                },
            )
        return alert

    alert = Alert(
        dedupe_key=key,
        alert_type=alert_type,
        rule_id=rule_id,
        plant_id=plant_id,
        line_id=line_id,
        metric=metric,
        scope_type=scope_type,
        scope_value=scope_value,
        severity=severity,
        title=title[:240],
        summary=summary,
        observed_value=observed,
        threshold_value=threshold,
        impact={"production": production, "defects": defects},
        evidence=evidence,
        recommended_check=recommended_check,
        confidence=confidence,
        status="open",
        detected_at=now,
        last_seen_at=now,
    )
    db.add(alert)
    db.flush()
    if settings.GEMINI_API_KEY:
        try:
            alert.ai_explanation = generate_custom_hardware_response(
                {
                    "alert_confirmed_by_python": True,
                    "alert_type": alert_type,
                    "metric": metric,
                    "scope": {"type": scope_type, "value": scope_value},
                    "period": period,
                    "observed_value": observed,
                    "threshold_value": threshold,
                    "summary": summary,
                    "impact": {"production": production, "defects": defects},
                    "evidence": evidence,
                },
                (
                    "Giải thích ngắn gọn cảnh báo đã được Python xác nhận và đề xuất "
                    "các bước kiểm tra. Chỉ dựa trên bằng chứng được cung cấp; không "
                    "thay đổi quyết định cảnh báo, không bịa số liệu, không khẳng định "
                    "quan hệ nhân quả nếu dữ liệu không chứng minh. Nếu thiếu dữ liệu, "
                    "nêu rõ giới hạn."
                ),
            )
            alert.ai_explanation_status = "available"
        except Exception:
            alert.ai_explanation_status = "unavailable"
            logger.exception(
                "AI explanation unavailable for Python-confirmed alert %s",
                alert.id,
            )
    else:
        alert.ai_explanation_status = "not_configured"
    record_alert_event(
        db,
        alert,
        event_type="detected",
        from_status=None,
        to_status="open",
        actor_user_id=None,
        note="Cảnh báo được xác nhận bởi bộ đánh giá Python.",
        event_data={"alert_type": alert_type, "metric": metric, "period": period},
    )
    return alert


def evaluate_monitoring_dataset(
    db: Session,
    dataset: MonitoringDataset,
) -> dict[str, int]:
    records = dataset.data_rows
    if not records:
        raise ValueError("Snapshot dữ liệu giám sát không có dòng dữ liệu.")
    frame = pd.DataFrame.from_records(records)
    if "Date" not in frame.columns or "Production" not in frame.columns:
        raise ValueError("Snapshot cần có cột Date và Production.")
    try:
        series = _series_for_records(records, dataset.granularity)
    except ValueError as exc:
        raise ValueError(f"Không thể đánh giá snapshot: {exc}") from exc
    if not series:
        raise ValueError("Snapshot không có kỳ dữ liệu hợp lệ để đánh giá.")
    latest = series[-1]
    latest_period = str(latest["period_start"])
    emitted = 0

    rules = db.scalars(
        select(AlertRule).where(
            AlertRule.enabled.is_(True),
            AlertRule.plant_id == dataset.plant_id,
        )
    ).all()
    for rule in rules:
        if rule.line_id is not None and rule.line_id != dataset.line_id:
            continue
        selected = records
        if rule.product:
            selected = [
                row
                for row in records
                if _product_value(row).casefold() == rule.product.strip().casefold()
            ]
        if not selected:
            continue
        rule_series = _series_for_records(selected, dataset.granularity)
        if not rule_series:
            continue
        point = rule_series[-1]
        value = _safe_float(point.get(rule.metric))
        if value is None:
            continue
        exceeded = {
            "gt": value > rule.threshold,
            "gte": value >= rule.threshold,
            "lt": value < rule.threshold,
            "lte": value <= rule.threshold,
        }[rule.operator]
        if not exceeded:
            continue
        product_label = f" / {rule.product}" if rule.product else ""
        scope_type = "product" if rule.product else (
            "line" if rule.line_id else "plant"
        )
        summary = (
            f"{METRIC_LABELS[rule.metric]} đạt {value:g}, "
            f"vi phạm ngưỡng {rule.operator} {rule.threshold:g}."
        )
        _emit_alert(
            db,
            plant_id=dataset.plant_id,
            line_id=dataset.line_id,
            alert_type="threshold",
            rule_id=rule.id,
            metric=rule.metric,
            scope_type=scope_type,
            scope_value=rule.product,
            severity=rule.severity,
            title=f"Vượt ngưỡng: {rule.name}{product_label}",
            summary=summary,
            observed=value,
            threshold=rule.threshold,
            period=point["period_start"],
            production=_safe_float(point.get("production")) or 0,
            defects=_safe_float(point.get("defects")) or 0,
            evidence=[
                {"period": point["period_start"], "value": value, "rule": rule.operator},
                {"rows_in_period": point["rows_in_period"]},
            ],
            recommended_check=f"Kiểm tra {METRIC_LABELS[rule.metric]} trong kỳ {point['period_start']} và xác nhận nguyên nhân tại hiện trường.",
            confidence="high",
        )
        emitted += 1

    for candidate in _statistical_candidates(records, dataset.granularity):
        metric = candidate["metric"]
        method = (
            "Rolling IQR + Z-score"
            if candidate["iqr_triggered"] and candidate["z_score_triggered"]
            else "Rolling IQR"
            if candidate["iqr_triggered"]
            else "Z-score"
        )
        severity = "high" if candidate["iqr_triggered"] and candidate["z_score_triggered"] else "medium"
        scope = candidate["scope_value"]
        scope_label = f"{candidate['scope_type']} {scope}" if scope else "toàn nhà máy"
        baseline = candidate["baseline"]
        evidence = [
            {"period": candidate["period"], "observed": candidate["observed"]},
            {
                "baseline_observations": len(baseline),
                "baseline_type": candidate.get("baseline_type", "periods"),
                "baseline_mean": candidate["mean"],
                "iqr_lower": candidate["lower"],
                "iqr_upper": candidate["upper"],
                "z_score": candidate["z_score"],
                "detectors": method,
            },
        ]
        summary = (
            f"{METRIC_LABELS[metric]} tại {scope_label} bất thường từ kỳ "
            f"{candidate['period']}: {candidate['observed']:g}; "
            f"nền lịch sử {candidate['mean']:g} (n={len(baseline)} "
            f"{candidate.get('baseline_type', 'kỳ')}). "
            "Đây là kết luận thống kê do Python."
        )
        _emit_alert(
            db,
            plant_id=dataset.plant_id,
            line_id=dataset.line_id,
            alert_type="statistical",
            rule_id=None,
            metric=metric,
            scope_type=candidate["scope_type"],
            scope_value=scope,
            severity=severity,
            title=f"Bất thường thống kê: {METRIC_LABELS[metric]} — {scope_label}",
            summary=summary,
            observed=candidate["observed"],
            threshold=None,
            period=candidate["period"],
            production=candidate["production"],
            defects=candidate["defects"],
            evidence=evidence,
            recommended_check=f"Ưu tiên đối chiếu dữ liệu và hồ sơ batch/supplier liên quan trong kỳ {candidate['period']}; so sánh với các kỳ nền trước đó.",
            confidence="high" if severity == "high" else "medium",
        )
        emitted += 1

    dataset.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    return {"evaluated_periods": 1, "alerts_detected": emitted}


def evaluate_due_datasets() -> None:
    with postgres_advisory_lock(DATASET_EVALUATION_LOCK_KEY) as acquired:
        if not acquired:
            return
        _evaluate_due_datasets()


def _evaluate_due_datasets() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        datasets = db.scalars(
            select(MonitoringDataset)
            .where(MonitoringDataset.enabled.is_(True))
            .order_by(
                MonitoringDataset.created_at.desc(),
                MonitoringDataset.id.desc(),
            )
        ).all()
        newest_by_scope: set[tuple[int, int | None]] = set()
        for dataset in datasets:
            scope = (dataset.plant_id, dataset.line_id)
            if scope in newest_by_scope:
                continue
            newest_by_scope.add(scope)
            interval = SCHEDULE_SECONDS[dataset.schedule_unit] * dataset.schedule_interval
            if dataset.last_checked_at is not None:
                checked = dataset.last_checked_at
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=timezone.utc)
                if now - checked < timedelta(seconds=interval):
                    continue
            try:
                evaluate_monitoring_dataset(db, dataset)
            except Exception:
                db.rollback()
                logger.exception(
                    "Scheduled monitoring evaluation failed for dataset %s",
                    dataset.id,
                )


def _send_email(
    user: AlertUser,
    alert: Alert,
    delivery_key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if not settings.ALERT_SMTP_HOST or not settings.ALERT_SMTP_FROM:
        raise RuntimeError("SMTP is not configured.")
    if not settings.ALERT_SMTP_USE_TLS and settings.ALERT_SMTP_PORT != 465:
        raise RuntimeError("SMTP TLS is required.")
    message = EmailMessage()
    message["Subject"] = f"[{alert.severity.upper()}] {alert.title}"
    message["From"] = settings.ALERT_SMTP_FROM
    message["To"] = user.email
    message["Message-ID"] = (
        f"<{hashlib.sha256(delivery_key.encode('utf-8')).hexdigest()}@alert.local>"
    )
    event_summary = (
        f"Sự kiện: {payload['event_type']} "
        f"({payload.get('from_status') or '-'} → {payload.get('to_status') or '-'})"
        if payload and payload.get("event_type")
        else None
    )
    event_data = payload.get("event_data", {}) if payload else {}
    if event_data.get("previous_severity") and event_data.get("severity"):
        event_summary = (
            f"Mức độ cảnh báo: {event_data['previous_severity']} "
            f"→ {event_data['severity']}"
        )
    message.set_content(
        "\n".join(
            item
            for item in (
                alert.summary,
                event_summary,
                f"Chỉ số: {alert.metric} = {alert.observed_value:g}",
                f"Phạm vi: {alert.scope_type} {alert.scope_value or ''}".strip(),
                f"Trạng thái: {(payload or {}).get('to_status') or alert.status}",
                f"Kiểm tra đề xuất: {alert.recommended_check}",
                f"Diễn giải AI (nếu có): {alert.ai_explanation or 'Chưa khả dụng'}",
            )
            if item
        )
    )
    context = ssl.create_default_context()
    if settings.ALERT_SMTP_PORT == 465:
        with smtplib.SMTP_SSL(
            settings.ALERT_SMTP_HOST,
            settings.ALERT_SMTP_PORT,
            timeout=8,
            context=context,
        ) as client:
            if settings.ALERT_SMTP_USERNAME:
                client.login(
                    settings.ALERT_SMTP_USERNAME,
                    settings.ALERT_SMTP_PASSWORD or "",
                )
            client.send_message(message)
        return
    with smtplib.SMTP(
        settings.ALERT_SMTP_HOST,
        settings.ALERT_SMTP_PORT,
        timeout=8,
    ) as client:
        if settings.ALERT_SMTP_USE_TLS:
            client.starttls(context=context)
        if settings.ALERT_SMTP_USERNAME:
            client.login(
                settings.ALERT_SMTP_USERNAME,
                settings.ALERT_SMTP_PASSWORD or "",
            )
        client.send_message(message)


def _resolve_public_webhook_target(target: str):
    parsed = urlsplit(target)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RuntimeError("Webhook must be configured with a valid HTTPS URL.")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise RuntimeError("Webhook URL contains an invalid port.") from exc
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise RuntimeError("Webhook hostname could not be resolved.") from exc
    if not addresses:
        raise RuntimeError("Webhook hostname has no address records.")

    for family, socktype, protocol, _, address in addresses:
        try:
            resolved_ip = ipaddress.ip_address(address[0].split("%", 1)[0])
        except ValueError as exc:
            raise RuntimeError("Webhook hostname resolved to an invalid address.") from exc
        if not resolved_ip.is_global:
            raise RuntimeError("Webhook must resolve only to public IP addresses.")
    return parsed, port, addresses


def _send_webhook(
    alert: Alert,
    delivery_key: str,
    event_payload: dict[str, Any] | None = None,
) -> None:
    target = settings.ALERT_WEBHOOK_URL or ""
    parsed, port, addresses = _resolve_public_webhook_target(target)
    payload = {
        "id": alert.id,
        "type": alert.alert_type,
        "severity": alert.severity,
        "status": alert.status,
        "title": alert.title,
        "summary": alert.summary,
        "metric": alert.metric,
        "scope": {"type": alert.scope_type, "value": alert.scope_value},
        "observed_value": alert.observed_value,
        "threshold_value": alert.threshold_value,
        "impact": alert.impact,
        "evidence": alert.evidence,
        "recommended_check": alert.recommended_check,
        "detected_at": (
            alert.detected_at.isoformat() if alert.detected_at else None
        ),
    }
    if event_payload:
        payload["event"] = event_payload
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    context = ssl.create_default_context()
    connection = None
    errors = []
    for family, socktype, protocol, _, address in addresses:
        raw_socket = socket.socket(family, socktype, protocol)
        try:
            raw_socket.settimeout(8)
            raw_socket.connect(address)
            peer_ip = ipaddress.ip_address(
                raw_socket.getpeername()[0].split("%", 1)[0]
            )
            if not peer_ip.is_global:
                raise RuntimeError("Webhook connection reached a non-public IP.")
            tls_socket = context.wrap_socket(
                raw_socket,
                server_hostname=parsed.hostname,
            )
            connection = http.client.HTTPSConnection(
                parsed.hostname,
                port,
                timeout=8,
                context=context,
            )
            connection.sock = tls_socket
            break
        except Exception as exc:
            raw_socket.close()
            if not isinstance(exc, OSError):
                raise
            errors.append(exc)
    if connection is None:
        raise RuntimeError("Could not connect to the public webhook host.") from (
            errors[-1] if errors else None
        )

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": delivery_key,
            },
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Webhook returned HTTP {response.status}.")
    finally:
        connection.close()


def deliver_pending_alert_notifications() -> None:
    with postgres_advisory_lock(ALERT_DELIVERY_LOCK_KEY) as acquired:
        if not acquired:
            return
        _deliver_pending_alert_notifications()


def _deliver_pending_alert_notifications() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        deliveries = db.scalars(
            select(AlertDelivery)
            .where(
                AlertDelivery.status == "pending",
                AlertDelivery.attempt_count < MAX_DELIVERY_ATTEMPTS,
                (AlertDelivery.next_attempt_at.is_(None))
                | (AlertDelivery.next_attempt_at <= now),
            )
            .order_by(AlertDelivery.created_at)
            .limit(100)
        ).all()
        for delivery in deliveries:
            alert = db.get(Alert, delivery.alert_id)
            user = (
                db.get(AlertUser, delivery.recipient_user_id)
                if delivery.recipient_user_id
                else None
            )
            try:
                if alert is None:
                    raise RuntimeError("Alert no longer exists.")
                if delivery.channel == "email":
                    if user is None:
                        raise RuntimeError("Email recipient no longer exists.")
                    if not user.is_active or not user.email_notifications:
                        delivery.status = "cancelled"
                        db.commit()
                        continue
                    if user.role != "admin" and db.scalar(
                        select(AlertUserPlant.id).where(
                            AlertUserPlant.user_id == user.id,
                            AlertUserPlant.plant_id == alert.plant_id,
                        )
                    ) is None:
                        delivery.status = "cancelled"
                        db.commit()
                        continue
                    _send_email(
                        user,
                        alert,
                        delivery.delivery_key,
                        delivery.payload,
                    )
                elif delivery.channel == "webhook":
                    _send_webhook(
                        alert,
                        delivery.delivery_key,
                        delivery.payload,
                    )
                else:
                    raise RuntimeError("Unknown notification channel.")
                delivery.status = "sent"
                delivery.sent_at = now
                delivery.last_error = None
            except Exception as exc:
                delivery.attempt_count += 1
                delivery.last_error = type(exc).__name__[:300]
                if delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
                    delivery.status = "failed"
                    logger.error(
                        "Alert %s delivery via %s exhausted retries (%s)",
                        delivery.alert_id,
                        delivery.channel,
                        type(exc).__name__,
                    )
                else:
                    delay = min(60 * 2 ** (delivery.attempt_count - 1), 3600)
                    delivery.next_attempt_at = now + timedelta(seconds=delay)
                    logger.warning(
                        "Alert %s delivery via %s failed; retry scheduled (%s)",
                        delivery.alert_id,
                        delivery.channel,
                        type(exc).__name__,
                    )
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Could not persist delivery result for alert %s",
                    delivery.alert_id,
                )


def reopen_expired_snoozes() -> None:
    with postgres_advisory_lock(SNOOZE_EXPIRY_LOCK_KEY) as acquired:
        if not acquired:
            return
        _reopen_expired_snoozes()


def _reopen_expired_snoozes() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        alerts = db.scalars(
            select(Alert).where(
                Alert.status == "snoozed",
                Alert.snoozed_until.is_not(None),
                Alert.snoozed_until <= now,
            )
        ).all()
        for alert in alerts:
            previous = alert.status
            alert.status = "open"
            alert.snoozed_until = None
            record_alert_event(
                db,
                alert,
                event_type="snooze_expired",
                from_status=previous,
                to_status="open",
                actor_user_id=None,
                note="Thời gian tạm hoãn đã kết thúc.",
            )
        if alerts:
            db.commit()
