"""Settings, read from environment variables (systemd loads them from config.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.getenv(name, default)))


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Config:
    db_path: Path
    rbu_storage_state: Path
    rbu_app_url: str | None  # RBU's web app; kept out of the code
    rbu_api_url: str | None  # RBU's JSON API
    chromium_path: str | None
    timezone: str
    team_ids: tuple[int, ...]  # empty = every team the RBU account can see
    google_service_account_file: Path | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    admin_emails: frozenset[str]
    contact_name: str
    login_command: str
    public_url: str
    cf_team_domain: str | None
    cf_audience: str | None
    dev_user_email: str | None  # local development only: skips Cloudflare Access

    @property
    def google_enabled(self) -> bool:
        return self.google_service_account_file is not None and self.google_service_account_file.exists()

    @property
    def public_host(self) -> str:
        return self.public_url.split("://", 1)[-1].rstrip("/")


def load() -> Config:
    teams = tuple(int(t) for t in os.getenv("RBU_TEAM_IDS", "").replace(" ", "").split(",") if t)
    sa_file = _optional("GOOGLE_SERVICE_ACCOUNT_FILE")
    return Config(
        db_path=_path("VAKT_DB", "~/.local/share/vaktkalender/vaktkalender.db"),
        rbu_storage_state=_path("RBU_STORAGE_STATE", "~/.config/vaktkalender/storage_state.json"),
        rbu_app_url=_optional("RBU_APP_URL"),
        rbu_api_url=(_optional("RBU_API_URL") or "").rstrip("/") or None,
        chromium_path=_optional("CHROMIUM_PATH"),
        timezone=os.getenv("TIMEZONE", "Europe/Oslo"),
        team_ids=teams,
        google_service_account_file=Path(os.path.expanduser(sa_file)) if sa_file else None,
        telegram_bot_token=_optional("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_optional("TELEGRAM_CHAT_ID"),
        admin_emails=frozenset(e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()),
        contact_name=os.getenv("CONTACT_NAME", "administratoren"),
        login_command=os.getenv("LOGIN_COMMAND", "./login_rbu.sh"),
        public_url=os.getenv("PUBLIC_URL", "http://localhost:8090").rstrip("/"),
        cf_team_domain=_optional("CF_ACCESS_TEAM_DOMAIN"),
        cf_audience=_optional("CF_ACCESS_AUD"),
        dev_user_email=_optional("DEV_USER_EMAIL"),
    )
