import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Sends a message via the Telegram Bot API. Returns True on success.

    Contacts get their chat_id by messaging the configured bot once and an
    operator reading the chat_id off /getUpdates - there is no in-app
    onboarding flow yet, see README.
    """
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping message to %s: %s", chat_id, text)
        return False

    url = TELEGRAM_API_URL.format(token=settings.telegram_bot_token)
    try:
        response = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10.0)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram message to chat_id=%s", chat_id)
        return False
