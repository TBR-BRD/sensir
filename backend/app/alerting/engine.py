"""Periodic evaluation of each household's observation window(s).

For every ObservationWindow instance covering "now", counts IR events seen
so far. If the count already meets the expectation, the window resolves as
positive immediately (even before it ends). If the window has ended without
enough activity, it resolves as negative and active contacts are alerted -
mirroring the positive/negative rule check described in Sensir Dokumentation
final.pdf section 1.1.

The expectation is the ML model's prediction (app/ml/window_model.py) once
trained, otherwise the window's fixed min_actions.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.config import settings
from app.db import session_scope
from app.ml import window_model
from app.models import (
    ActivityCheck,
    AlertLog,
    CheckStatus,
    Contact,
    Household,
    IrEvent,
    ObservationWindow,
    Sensor,
)

logger = logging.getLogger(__name__)


def run_periodic_check() -> None:
    with session_scope() as session:
        for household in session.execute(select(Household)).scalars().all():
            try:
                _check_household(session, household)
            except Exception:
                logger.exception("household %s: activity check failed", household.id)
        session.commit()


def _todays_windows(session: Session, household_id: int, weekday: int) -> list[ObservationWindow]:
    return (
        session.execute(
            select(ObservationWindow).where(
                ObservationWindow.household_id == household_id,
                ObservationWindow.is_active.is_(True),
                (ObservationWindow.weekday == weekday) | (ObservationWindow.weekday.is_(None)),
            )
        )
        .scalars()
        .all()
    )


def active_window(session: Session, household_id: int, now_local: dt.datetime) -> ObservationWindow | None:
    """Public: the window currently in progress right now, if any - used by
    the /status endpoint and the dashboard to show what's being watched.
    Not used for alerting itself, see _check_household: a window whose end
    has already passed must still be evaluated even though it's no longer
    "current" by this definition."""
    now_t = now_local.time()
    for w in _todays_windows(session, household_id, now_local.weekday()):
        if w.start_time <= now_t <= w.end_time:
            return w
    return None


def _count_events(session: Session, household_id: int, start: dt.datetime, end: dt.datetime) -> int:
    return session.execute(
        select(func.count(IrEvent.id))
        .join(Sensor, Sensor.id == IrEvent.sensor_id)
        .where(Sensor.household_id == household_id, IrEvent.received_at >= start, IrEvent.received_at < end)
    ).scalar_one()


def _sufficient(household_id: int, action_count: int, start_t: dt.time, end_t: dt.time, min_actions: int) -> bool:
    expected = window_model.expected_events(household_id, window_model.minute_of_day(start_t), window_model.minute_of_day(end_t))
    if expected is not None and expected >= 1:
        return action_count >= expected * window_model.ANOMALY_RATIO
    return action_count >= min_actions


def _check_household(session: Session, household: Household) -> None:
    tz = ZoneInfo(household.timezone)
    now_local = dt.datetime.now(tz)

    windows = _todays_windows(session, household.id, now_local.weekday())
    if not windows:
        # no manual config yet - evaluate the single default window instead
        _evaluate_window(
            session, household, None,
            settings.default_window_start, settings.default_window_end, settings.default_min_actions,
            now_local, tz,
        )
        return

    for w in windows:
        _evaluate_window(session, household, w.id, w.start_time, w.end_time, w.min_actions, now_local, tz)


def _evaluate_window(
    session: Session,
    household: Household,
    window_id: int | None,
    start_t: dt.time,
    end_t: dt.time,
    min_actions: int,
    now_local: dt.datetime,
    tz: ZoneInfo,
) -> None:
    today = now_local.date()
    period_start = dt.datetime.combine(today, start_t, tzinfo=tz)
    period_end = dt.datetime.combine(today, end_t, tzinfo=tz)
    if now_local < period_start:
        return  # today's instance of this window hasn't started yet

    already_checked = session.execute(
        select(ActivityCheck).where(
            ActivityCheck.household_id == household.id,
            ActivityCheck.period_start == period_start,
            ActivityCheck.period_end == period_end,
        )
    ).scalar_one_or_none()
    if already_checked is not None:
        return  # today's instance of this window is already resolved

    action_count = _count_events(session, household.id, period_start, min(now_local, period_end))
    sufficient = _sufficient(household.id, action_count, start_t, end_t, min_actions)

    if sufficient:
        _record_check(session, household.id, window_id, period_start, period_end, action_count, CheckStatus.positive)
        return

    if now_local < period_end:
        return  # not enough activity yet, but the window is still open - keep waiting

    _record_check(session, household.id, window_id, period_start, period_end, action_count, CheckStatus.negative)
    _send_alert(session, household, action_count, min_actions)


def _record_check(
    session: Session,
    household_id: int,
    window_id: int | None,
    period_start: dt.datetime,
    period_end: dt.datetime,
    action_count: int,
    status: CheckStatus,
) -> None:
    session.add(
        ActivityCheck(
            household_id=household_id,
            window_id=window_id,
            period_start=period_start,
            period_end=period_end,
            action_count=action_count,
            status=status,
        )
    )
    session.flush()


def _send_alert(session: Session, household: Household, action_count: int, min_actions: int) -> None:
    message = (
        f"Bitte melde dich bei {household.name}! Keine Fernbedienungsaktivität im "
        f"erwarteten Zeitfenster ({action_count} von mind. {min_actions} erwarteten Aktionen)."
    )
    contacts = session.execute(
        select(Contact).where(Contact.household_id == household.id, Contact.is_active.is_(True))
    ).scalars().all()
    if not contacts:
        logger.warning("household %s: negative check but no active contacts configured", household.id)
        return
    for contact in contacts:
        success = send_telegram_message(contact.telegram_chat_id, message) if contact.telegram_chat_id else False
        session.add(AlertLog(household_id=household.id, contact_id=contact.id, message=message, success=success))
