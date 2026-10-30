"""Receive Telegram commands and expose the Heatmap reply-keyboard button."""

from __future__ import annotations

import json
import logging
import threading

import httpx

from app.alerting.telegram import HEATMAP_REPLY_KEYBOARD, TELEGRAM_API_URL, send_telegram_message
from app.config import settings
from app.db import session_scope
from app.weekly_heatmap import is_chat_authorized_for_all_active_households, send_weekly_heatmaps_to_chat

logger = logging.getLogger(__name__)


class TelegramBotListener:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.telegram_bot_token:
            logger.info("Telegram command listener disabled: bot token is not configured")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, name="telegram-command-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _poll(self) -> None:
        url = TELEGRAM_API_URL.format(token=settings.telegram_bot_token, method="getUpdates")
        try:
            with httpx.Client(timeout=httpx.Timeout(35.0)) as client:
                offset = self._skip_pending_updates(client, url)
                while not self._stop.is_set():
                    try:
                        updates = self._get_updates(client, url, offset, timeout=25)
                        for update in updates:
                            offset = int(update["update_id"]) + 1
                            try:
                                self._handle_update(update)
                            except Exception as exc:  # noqa: BLE001
                                logger.error("Telegram update handler failed (%s)", type(exc).__name__)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 409:
                            logger.error("Telegram polling stopped: a webhook or another getUpdates client is active")
                            return
                        logger.warning("Telegram polling HTTP error (status=%s)", exc.response.status_code)
                        self._stop.wait(5)
                    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                        logger.warning("Telegram polling error (%s)", type(exc).__name__)
                        self._stop.wait(5)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.error("Telegram polling unavailable: a webhook or another getUpdates client is active")
            else:
                logger.warning("Telegram polling setup HTTP error (status=%s)", exc.response.status_code)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Telegram polling setup error (%s)", type(exc).__name__)

    def _skip_pending_updates(self, client: httpx.Client, url: str) -> int | None:
        """Discard commands sent before this listener started, avoiding delayed sends."""
        updates = self._get_updates(client, url, offset=-1, timeout=0, limit=1)
        return int(updates[-1]["update_id"]) + 1 if updates else None

    @staticmethod
    def _get_updates(
        client: httpx.Client,
        url: str,
        offset: int | None,
        timeout: int,
        limit: int | None = None,
    ) -> list[dict]:
        params: dict[str, int | str] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            params["offset"] = offset
        if limit is not None:
            params["limit"] = limit
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram returned ok=false")
        return payload.get("result", [])

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        text = message.get("text")
        chat = message.get("chat") or {}
        if not isinstance(text, str) or chat.get("id") is None:
            return

        chat_id = str(chat["id"])
        parts = text.strip().casefold().split(maxsplit=1)
        if not parts:
            return
        command = parts[0].split("@", 1)[0]
        if command == "/id":
            send_telegram_message(chat_id, f"Deine Telegram-Chat-ID für SensIR: {chat_id}")
            return
        if command in {"/start", "/help", "hallo", "hello", "hi"}:
            with session_scope() as session:
                authorized = is_chat_authorized_for_all_active_households(session, chat_id)
            if authorized:
                send_telegram_message(
                    chat_id,
                    "SensIR ist bereit. Tippe auf Heatmap oder sende /heatmap, um die Wochenbilder zu erhalten.",
                    reply_markup=HEATMAP_REPLY_KEYBOARD,
                )
            else:
                send_telegram_message(
                    chat_id,
                    f"Dieser Chat ist nicht als aktiver Kontakt in allen aktiven Familien eingetragen. Deine Chat-ID: {chat_id}",
                )
            return

        if command in {"heatmap", "/heatmap"}:
            with session_scope() as session:
                result = send_weekly_heatmaps_to_chat(session, chat_id)
            if result["not_configured"]:
                response = "Der Telegram-Bot ist nicht konfiguriert."
            elif not result["active_households"]:
                response = "Es sind keine aktiven Familien vorhanden."
            elif not result["authorized"]:
                response = "Für die Heatmaps musst du als aktiver Telegram-Kontakt in allen aktiven Familien eingetragen sein."
            else:
                response = (
                    f"Wochen-Heatmaps: {result['sent']} erfolgreich, "
                    f"{result['failed']} fehlgeschlagen."
                )
            send_telegram_message(chat_id, response, reply_markup=HEATMAP_REPLY_KEYBOARD)


telegram_bot = TelegramBotListener()
