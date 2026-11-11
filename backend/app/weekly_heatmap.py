"""Build and send one weekly activity heatmap per active household."""

from __future__ import annotations

import io
import logging
import re

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_photo
from app.config import settings
from app.models import AlertLog, Contact, Household
from app.status_service import weekly_activity_chart

logger = logging.getLogger(__name__)

IMAGE_WIDTH = 1520
IMAGE_HEIGHT = 580
GRID_LEFT = 205
CELL_WIDTH = 48
CELL_HEIGHT = 40
CELL_GAP = 16
TV_COLOR = (21, 101, 192)
TV_BAR_HEIGHT = 8
GRID_TOP = 112


def build_weekly_heatmap_png(chart: dict) -> bytes:
    """Render the current Monday–Sunday hourly matrix as a Telegram-ready PNG."""
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#ffffff")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=28)
    label_font = ImageFont.load_default(size=16)
    small_font = ImageFont.load_default(size=13)

    draw.text((42, 28), "SensIR Wochen-Heatmap", fill="#202124", font=title_font)
    period = f"{chart['week_start']:%d.%m.%Y} bis {chart['week_end']:%d.%m.%Y}  |  Ortszeit {chart['timezone']}"
    draw.text((44, 70), period, fill="#666a70", font=label_font)

    for hour in range(0, 24, 2):
        label = f"{hour:02d}:00"
        x = GRID_LEFT + hour * CELL_WIDTH + CELL_WIDTH // 2
        bounds = draw.textbbox((0, 0), label, font=small_font)
        draw.text((x - (bounds[2] - bounds[0]) // 2, GRID_TOP - 27), label, fill="#55585e", font=small_font)
    end_label = "24:00"
    end_bounds = draw.textbbox((0, 0), end_label, font=small_font)
    draw.text((GRID_LEFT + 24 * CELL_WIDTH - (end_bounds[2] - end_bounds[0]) // 2, GRID_TOP - 27), end_label, fill="#55585e", font=small_font)

    peak = max(1, chart["max_hourly_count"])
    for day_index, day in enumerate(chart["days"]):
        top = GRID_TOP + day_index * (CELL_HEIGHT + CELL_GAP)
        draw.text((42, top + 11), day["label"], fill="#33363a", font=label_font)
        for hour, count in enumerate(day["hourly_counts"]):
            left = GRID_LEFT + hour * CELL_WIDTH
            if count:
                ratio = count / peak
                start_color = (250, 226, 239)
                end_color = (226, 0, 116)
                color = tuple(round(a + (b - a) * ratio) for a, b in zip(start_color, end_color))
            else:
                color = (241, 242, 245)
            draw.rounded_rectangle(
                (left, top, left + CELL_WIDTH - 3, top + CELL_HEIGHT),
                radius=5,
                fill=color,
                outline="#ffffff",
                width=2,
            )
        tv_top = top + CELL_HEIGHT + 3
        for interval in day.get("tv_intervals", []):
            start_minute = min(interval["start_minute"], 1440)
            end_minute = min(interval["end_minute"], 1440)
            left = GRID_LEFT + start_minute * 24 * CELL_WIDTH / 1440
            right = max(left + 2, GRID_LEFT + end_minute * 24 * CELL_WIDTH / 1440)
            draw.rounded_rectangle((left, tv_top, right, tv_top + TV_BAR_HEIGHT), radius=3, fill=TV_COLOR)
        if day.get("tv_minutes"):
            draw.text(
                (GRID_LEFT + 24 * CELL_WIDTH + 12, top + 12),
                f"TV {day['tv_label']}",
                fill=TV_COLOR,
                font=small_font,
            )

    legend_y = GRID_TOP + 7 * (CELL_HEIGHT + CELL_GAP) + 4
    draw.text((GRID_LEFT, legend_y), "Weniger", fill="#666a70", font=small_font)
    legend_left = GRID_LEFT + 72
    legend_width = 200
    for step in range(40):
        ratio = step / 39
        start_color = (250, 226, 239)
        end_color = (226, 0, 116)
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(start_color, end_color))
        x = legend_left + step * legend_width // 40
        draw.line((x, legend_y + 8, x, legend_y + 24), fill=color, width=6)
    draw.text((legend_left + legend_width + 10, legend_y), "Mehr Ereignisse pro Stunde", fill="#666a70", font=small_font)
    tv_legend_left = legend_left + legend_width + 230
    draw.rounded_rectangle((tv_legend_left, legend_y + 5, tv_legend_left + 30, legend_y + 5 + TV_BAR_HEIGHT), radius=3, fill=TV_COLOR)
    draw.text(
        (tv_legend_left + 40, legend_y),
        f"TV an (Woche gesamt: {chart.get('tv_label', '0 min')})",
        fill="#666a70",
        font=small_font,
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _build_household_heatmap(session: Session, household: Household) -> tuple[dict, str, bytes, str]:
    chart = weekly_activity_chart(session, household)
    png = build_weekly_heatmap_png(chart)
    slug = re.sub(r"[^a-z0-9]+", "-", household.name.lower()).strip("-") or f"familie-{household.id}"
    filename = f"sensir-heatmap-{slug}-{chart['week_start']:%Y-%m-%d}.png"
    caption = (
        f"📊 SensIR Wochen-Heatmap · {household.name}\n"
        f"{chart['week_start']:%d.%m.%Y}–{chart['week_end']:%d.%m.%Y} · "
        f"{chart['event_count']} Ereignisse · TV {chart['tv_label']}"
    )
    return chart, filename, png, caption


def _active_households_and_chat_contacts(
    session: Session, chat_id: str
) -> tuple[list[Household], dict[int, Contact]]:
    households = session.execute(
        select(Household).where(Household.is_active.is_(True)).order_by(Household.id)
    ).scalars().all()
    contacts = session.execute(
        select(Contact)
        .join(Household, Household.id == Contact.household_id)
        .where(
            Household.is_active.is_(True),
            Contact.is_active.is_(True),
            Contact.telegram_chat_id == chat_id,
        )
    ).scalars().all()
    return households, {contact.household_id: contact for contact in contacts}


def is_chat_authorized_for_all_active_households(session: Session, chat_id: str) -> bool:
    households, contacts = _active_households_and_chat_contacts(session, chat_id)
    return bool(households) and all(household.id in contacts for household in households)




def send_weekly_heatmaps(session: Session) -> dict[str, int | bool]:
    """Send one weekly heatmap to every active Telegram contact per active household."""
    result: dict[str, int | bool] = {
        "active_households": 0,
        "sent": 0,
        "failed": 0,
        "skipped_households": 0,
        "not_configured": not bool(settings.telegram_bot_token),
    }
    if result["not_configured"]:
        return result

    households = session.execute(
        select(Household).where(Household.is_active.is_(True)).order_by(Household.id)
    ).scalars().all()
    result["active_households"] = len(households)

    for household in households:
        contacts = session.execute(
            select(Contact).where(
                Contact.household_id == household.id,
                Contact.is_active.is_(True),
                Contact.telegram_chat_id.is_not(None),
            )
        ).scalars().all()
        contacts = [contact for contact in contacts if contact.telegram_chat_id]
        if not contacts:
            result["skipped_households"] += 1
            continue

        try:
            chart, filename, png, caption = _build_household_heatmap(session, household)
        except Exception:
            logger.exception("Failed to build weekly heatmap for household_id=%s", household.id)
            result["failed"] += len(contacts)
            continue

        for contact in contacts:
            success = send_telegram_photo(contact.telegram_chat_id, filename, png, caption)
            result["sent" if success else "failed"] += 1
            session.add(
                AlertLog(
                    household_id=household.id,
                    contact_id=contact.id,
                    message=f"[Wochen-Heatmap {chart['week_start']:%Y-%m-%d}–{chart['week_end']:%Y-%m-%d}] {filename}",
                    success=success,
                )
            )

    session.commit()
    return result


def send_weekly_heatmaps_to_chat(session: Session, chat_id: str) -> dict[str, int | bool]:
    """Send all active households' heatmaps only to an authorized requesting chat."""
    result: dict[str, int | bool] = {
        "authorized": False,
        "active_households": 0,
        "sent": 0,
        "failed": 0,
        "not_configured": not bool(settings.telegram_bot_token),
    }
    if result["not_configured"]:
        return result

    households, contacts = _active_households_and_chat_contacts(session, chat_id)
    result["active_households"] = len(households)
    if not households or any(household.id not in contacts for household in households):
        return result
    result["authorized"] = True

    for household in households:
        contact = contacts[household.id]
        try:
            chart, filename, png, caption = _build_household_heatmap(session, household)
        except Exception:
            logger.exception("Failed to build requested weekly heatmap for household_id=%s", household.id)
            result["failed"] += 1
            session.add(
                AlertLog(
                    household_id=household.id,
                    contact_id=contact.id,
                    message="[Wochen-Heatmap] Bild konnte nicht erstellt werden",
                    success=False,
                )
            )
            continue

        success = send_telegram_photo(chat_id, filename, png, caption)
        result["sent" if success else "failed"] += 1
        session.add(
            AlertLog(
                household_id=household.id,
                contact_id=contact.id,
                message=f"[Wochen-Heatmap {chart['week_start']:%Y-%m-%d}–{chart['week_end']:%Y-%m-%d}] {filename}",
                success=success,
            )
        )

    session.commit()
    return result
