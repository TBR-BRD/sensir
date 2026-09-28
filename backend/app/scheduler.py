import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import health
from app.alerting.engine import run_periodic_check
from app.config import settings
from app.db import session_scope
from app.export import export_and_send_all_households
from app.ml.window_model import train_all_household_models

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _train_models_job() -> None:
    with session_scope() as session:
        train_all_household_models(session)


def _weekly_export_job() -> None:
    with session_scope() as session:
        export_and_send_all_households(session)


def start() -> None:
    # Leichter, unabhängiger Heartbeat-Job für /healthz (siehe app/health.py)
    # - erkennt ein stilles Einfrieren des Prozesses (live beobachtet
    # 2026-09-28: alle Hintergrund-Threads standen 2h lang, ohne dass der
    # HTTP-Server das gemerkt hätte oder der Container abgestürzt wäre).
    scheduler.add_job(
        health.touch,
        trigger=IntervalTrigger(minutes=1),
        id="heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        run_periodic_check,
        trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        id="activity_check",
        replace_existing=True,
    )
    scheduler.add_job(
        _train_models_job,
        trigger=CronTrigger(hour=settings.ml_train_hour_utc, minute=0),
        id="train_models",
        replace_existing=True,
    )
    if settings.weekly_export_enabled:
        scheduler.add_job(
            _weekly_export_job,
            trigger=CronTrigger(
                day_of_week=settings.weekly_export_day_of_week,
                hour=settings.weekly_export_hour_utc,
                minute=0,
            ),
            id="weekly_export",
            replace_existing=True,
        )
    scheduler.start()
    logger.info(
        "scheduler started: activity check every %sm, model training daily at %s:00 UTC, "
        "weekly export %s",
        settings.check_interval_minutes,
        settings.ml_train_hour_utc,
        f"{settings.weekly_export_day_of_week} {settings.weekly_export_hour_utc}:00 UTC"
        if settings.weekly_export_enabled else "disabled",
    )


def stop() -> None:
    scheduler.shutdown(wait=False)
