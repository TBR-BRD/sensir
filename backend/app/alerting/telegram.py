import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Sends a message via the Telegram Bot API. Returns True on success.

    Contacts get their chat_id by messaging the configured bot once and an
    operator reading the chat_id off /getUpdates - there is no in-app
    onboarding flow yet, see README.
    """
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping message to %s: %s", chat_id, text)
        return False

    url = TELEGRAM_API_URL.format(token=settings.telegram_bot_token, method="sendMessage")
    try:
        response = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram message to chat_id=%s", chat_id)
        return False


def send_telegram_document(
    chat_id: str, filename: str, content: bytes, caption: str | None = None
) -> bool:
    """Sends a file (z. B. der wöchentliche CSV-Export) via die Telegram Bot
    API. Returns True on success. Siehe app/export.py für den Aufrufer."""
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping document to %s: %s", chat_id, filename)
        return False

    url = TELEGRAM_API_URL.format(token=settings.telegram_bot_token, method="sendDocument")
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    try:
        response = httpx.post(
            url,
            data=data,
            files={"document": (filename, content, "text/csv")},
            timeout=30.0,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram document to chat_id=%s", chat_id)
        return False
