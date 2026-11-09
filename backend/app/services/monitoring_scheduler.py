import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.monitoring_service import (
    deliver_pending_alert_notifications,
    evaluate_due_datasets,
    reopen_expired_snoozes,
)
from app.services.monitoring_lock import postgres_advisory_lock


logger = logging.getLogger(__name__)
SCHEDULER_LOCK_KEY = 0x4D4F4E49544F5201
scheduler = BackgroundScheduler(timezone="UTC")
_scheduler_lock_context = None
scheduler.add_job(
    evaluate_due_datasets,
    trigger=IntervalTrigger(seconds=60),
    id="intelligent-monitoring",
    name="Evaluate due monitoring snapshots",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)
scheduler.add_job(
    reopen_expired_snoozes,
    trigger=IntervalTrigger(seconds=30),
    id="alert-snooze-expiry",
    name="Reopen alerts after snooze expires",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)
scheduler.add_job(
    deliver_pending_alert_notifications,
    trigger=IntervalTrigger(seconds=30),
    id="alert-notification-delivery",
    name="Deliver pending alert notifications",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)


def start_scheduler() -> bool:
    global _scheduler_lock_context
    if scheduler.running:
        return True

    lock_context = postgres_advisory_lock(SCHEDULER_LOCK_KEY)
    if not lock_context.__enter__():
        lock_context.__exit__(None, None, None)
        logger.info("Monitoring scheduler is already owned by another worker.")
        return False

    _scheduler_lock_context = lock_context
    try:
        scheduler.start()
    except Exception:
        _scheduler_lock_context = None
        lock_context.__exit__(*sys.exc_info())
        raise
    return True


def stop_scheduler() -> None:
    global _scheduler_lock_context
    if scheduler.running:
        scheduler.shutdown(wait=True)
    if _scheduler_lock_context is not None:
        context = _scheduler_lock_context
        _scheduler_lock_context = None
        context.__exit__(None, None, None)
