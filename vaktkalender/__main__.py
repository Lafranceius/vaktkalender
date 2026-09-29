"""python -m vaktkalender <command>

  run                 fetch from RBU, store, sync Google (the daily job)
  sync                only re-sync Google calendars from the database
  login --out FILE    open a browser, log in to RBU, save the session (run on the Mac)
  test-telegram       send a test message
  check               verify RBU session, Google, Telegram and Cloudflare Access settings
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="vaktkalender")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("sync")
    login = sub.add_parser("login")
    login.add_argument("--out", type=Path, required=True)
    sub.add_parser("test-telegram")
    sub.add_parser("check")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from . import config

    if args.command == "login":
        from .rbu import login_interactively

        login_interactively(config.load(), args.out)
        print(f"Lagret RBU-innlogging til {args.out}")
        return 0

    from .db import Store

    cfg = config.load()
    if args.command == "test-telegram":
        from .telegram import notify

        if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
            print("FEIL TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID mangler")
            return 1
        return 0 if notify(cfg, "✅ Vaktkalender kan sende meldinger til deg.") else 1

    if args.command == "check":
        return check(cfg)

    store = Store(cfg.db_path)
    if args.command == "sync":
        from .job import sync_all

        ok, failed = sync_all(cfg, store)
        print(f"{ok} synket, {failed} feilet")
        return 1 if failed else 0

    from .job import run

    return 0 if run(cfg, store) else 1


def check(cfg) -> int:
    import json

    import requests

    from .rbu import session_expires_at

    problems = 0

    def report(ok: bool, text: str) -> None:
        nonlocal problems
        problems += not ok
        print(("OK   " if ok else "FEIL ") + text)

    expires = session_expires_at(cfg.rbu_storage_state)
    report(expires is not None, f"RBU-innlogging utløper {expires:%Y-%m-%d}" if expires else "RBU-innlogging mangler")
    report(bool(cfg.rbu_app_url and cfg.rbu_api_url), "RBU-adresser (RBU_APP_URL, RBU_API_URL)")

    if cfg.google_enabled:
        from .db import Store
        from .gcal import CALENDAR_NAME, GoogleCalendar

        try:
            google = GoogleCalendar(cfg.google_service_account_file)
            calendars = google.list_calendars()
            email = json.loads(cfg.google_service_account_file.read_text())["client_email"]
            report(True, f"Google: service account {email}")
            members = [m for m in Store(cfg.db_path).members() if m.google_calendar_id]
            for m in members:
                if m.sync_error:
                    report(False, f"Google-synk {m.code} ({m.email}): {m.sync_error}")
            report(True, f"Google-synk: {sum(not m.sync_error for m in members)} av {len(members)} OK")
            # A calendar no member points at never gets updates, yet whoever it is shared with still sees it.
            linked = {m.google_calendar_id for m in members}
            for cal in calendars:
                if cal.get("summary") == CALENDAR_NAME and cal["id"] not in linked:
                    shared = ", ".join(google.readers(cal["id"])) or "ingen"
                    report(False, f"Google: foreldreløs kalender delt med {shared}: {cal['id']}")
        except Exception as e:
            report(False, f"Google: {e}")
    else:
        report(False, "Google: service account-fil mangler")

    report(bool(cfg.telegram_bot_token and cfg.telegram_chat_id), "Telegram satt opp")

    if cfg.cf_team_domain and cfg.cf_audience:
        try:
            r = requests.get(f"https://{cfg.cf_team_domain}/cdn-cgi/access/certs", timeout=10)
            report(r.ok and "keys" in r.json(), f"Cloudflare Access: {cfg.cf_team_domain}")
        except (requests.RequestException, ValueError) as e:
            report(False, f"Cloudflare Access: {type(e).__name__}")
    else:
        report(False, "Cloudflare Access: CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD mangler")

    report(bool(cfg.admin_emails), f"Admin: {', '.join(sorted(cfg.admin_emails)) or 'ingen'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
