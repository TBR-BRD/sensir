"""Learns each household's normal daily remote-control activity pattern and
derives, from that, how many IR events to expect in a given time-of-day
interval - so the observation window and action threshold no longer have to
be configured by hand once enough history has built up (see the "Aussicht"
section in Sensir Dokumentation final.pdf: an AI should learn the daily
routine and flag deviations from it).

Approach: fit a 1D Gaussian KernelDensity over the minute-of-day of all past
IrEvents for a household. Its density, scaled by the total event count and
observed number of days, gives an expected event count for any interval.
Actual counts far below that expectation are flagged as anomalous.

Known limitation: no midnight-wraparound handling, i.e. a window crossing
00:00 will under-estimate density near the boundary. Acceptable for the PoC
target group (evening TV use, see personas), revisit if that changes.
"""

import datetime as dt
import logging
from pathlib import Path

import joblib
import numpy as np
from sklearn.neighbors import KernelDensity
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Household, SensorEvent, Sensor

logger = logging.getLogger(__name__)

KDE_BANDWIDTH_MINUTES = 30.0
ANOMALY_RATIO = 0.3  # actual/expected below this ratio counts as a deviation


def minute_of_day(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def _model_path(household_id: int) -> Path:
    d = Path(settings.ml_model_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"household_{household_id}.joblib"


def _event_minutes_of_day(session: Session, household_id: int) -> tuple[np.ndarray, dt.datetime | None, dt.datetime | None]:
    rows = (
        session.execute(
            select(SensorEvent.received_at)
            .join(Sensor, Sensor.id == SensorEvent.sensor_id)
            .where(Sensor.household_id == household_id, SensorEvent.safety.is_(False))
        )
        .scalars()
        .all()
    )
    if not rows:
        return np.array([]), None, None
    minutes = np.array([r.hour * 60 + r.minute + r.second / 60 for r in rows])
    return minutes, min(rows), max(rows)


def train_household_model(session: Session, household_id: int) -> dict | None:
    """Fits a KernelDensity model over time-of-day of past IR events for one
    household. Skips (returns None) until ML_MIN_SAMPLES events are
    available - the alerting engine falls back to the configured/default
    ObservationWindow rows until then.
    """
    minutes, first_seen, last_seen = _event_minutes_of_day(session, household_id)
    if minutes.size < settings.ml_min_samples:
        logger.info(
            "household %s: %d/%d events, not enough history to train yet",
            household_id,
            minutes.size,
            settings.ml_min_samples,
        )
        return None

    n_days = max((last_seen.date() - first_seen.date()).days + 1, 1)

    kde = KernelDensity(kernel="gaussian", bandwidth=KDE_BANDWIDTH_MINUTES)
    kde.fit(minutes.reshape(-1, 1))

    metadata = {
        "n_events": int(minutes.size),
        "n_days": n_days,
        "trained_at": dt.datetime.utcnow().isoformat(),
    }
    joblib.dump({"kde": kde, **metadata}, _model_path(household_id))
    logger.info(
        "household %s: trained activity model on %d events over %d days",
        household_id,
        minutes.size,
        n_days,
    )
    return metadata


def train_all_household_models(session: Session) -> None:
    household_ids = session.execute(select(Household.id)).scalars().all()
    for household_id in household_ids:
        try:
            train_household_model(session, household_id)
        except Exception:
            logger.exception("household %s: training failed", household_id)


def _load_model(household_id: int) -> dict | None:
    path = _model_path(household_id)
    if not path.exists():
        return None
    return joblib.load(path)


def has_model(household_id: int) -> bool:
    return _model_path(household_id).exists()


def expected_events(household_id: int, start_minute: int, end_minute: int) -> float | None:
    """Expected number of IR events in [start_minute, end_minute) of a
    typical day for this household. None if untrained. Does not handle
    intervals crossing midnight (start_minute must be < end_minute).
    """
    model = _load_model(household_id)
    if model is None:
        return None

    grid = np.linspace(start_minute, end_minute, num=max(int(end_minute - start_minute), 2)).reshape(-1, 1)
    log_density = model["kde"].score_samples(grid)
    density = np.exp(log_density)
    mass_fraction = float(np.trapz(density, grid.ravel()))
    return mass_fraction * model["n_events"] / model["n_days"]
