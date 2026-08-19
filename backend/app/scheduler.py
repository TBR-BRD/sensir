import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.alerting.engine import run_periodic_check
from app.config import settings
from app.db import session_scope
from app.ml.window_model import train_all_household_models

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _train_models_job() -> None:
    with session_scope() as session:
        train_all_household_models(session)


def start() -> None:
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
    scheduler.start()
    logger.info(
        "scheduler started: activity check every %sm, model training daily at %s:00 UTC",
        settings.check_interval_minutes,
        settings.ml_train_hour_utc,
    )


def stop() -> None:
    scheduler.shutdown(wait=False)
