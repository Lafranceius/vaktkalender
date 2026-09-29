"""Messages to the owner's phone. Silently does nothing if not configured."""

from __future__ import annotations

import logging

import requests

from .config import Config

log = logging.getLogger(__name__)


def notify(cfg: Config, text: str) -> bool:
    """True if Telegram accepted the message."""
    log.info("telegram: %s", text.splitlines()[0])
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        return False
    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage",
            json={"chat_id": cfg.telegram_chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        ).raise_for_status()
    except requests.RequestException as e:
        # Never log str(e): it contains the URL, and the URL contains the token.
        detail = e.response.text if e.response is not None else ""
        log.error("Telegram failed: %s %s", type(e).__name__, detail)
        return False
    return True
