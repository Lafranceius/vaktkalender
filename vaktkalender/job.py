"""The daily run: fetch from RBU, store, mirror to Google, tell the owner."""

from __future__ import annotations

import fcntl
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import rbu
from .config import Config
from .db import Member, Store, SuspiciousSnapshot
from .gcal import GoogleCalendar
from .shifts import scrape_window
from .telegram import notify

log = logging.getLogger(__name__)

MONTHS_NB = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"]
SESSION_WARNING = timedelta(days=7)


def sync_member(cfg: Config, store: Store, google: GoogleCalendar, member: Member) -> None:
    """Mirror one colleague's shifts into their Google calendar; record the outcome."""
    assert member.google_calendar_id
    try:
        google.mirror(member.google_calendar_id, store.shifts_for(member.code), cfg.timezone)
    except Exception as e:
        error = str(e)[:300]
        if error != member.sync_error:
            notify(cfg, f"⚠️ Google-synk feilet for {member.email} ({member.code}):\n{error}")
        store.record_sync(member.email, error)
        raise
    store.record_sync(member.email, None)


def sync_all(cfg: Config, store: Store) -> tuple[int, int]:
    if not cfg.google_enabled:
        return 0, 0
    google = GoogleCalendar(cfg.google_service_account_file)
    ok = failed = 0
    for member in store.members():
        if not member.google_calendar_id:
            continue
        try:
            sync_member(cfg, store, google, member)
            ok += 1
        except Exception:
            log.exception("sync failed for member")
            failed += 1
    return ok, failed


def _login_expired_text(cfg: Config, reason: str) -> str:
    return (
        "🔑 RBU-innloggingen har gått ut, så vaktene ble ikke oppdatert.\n\n"
        "Fiks (1 minutt) – i Terminal på Macen:\n"
        f"{cfg.login_command}\n\n"
        "Logg inn i vinduet som åpnes. Resten skjer automatisk.\n"
        f"({reason})"
    )


def _announce_new_month(cfg: Config, store: Store, today: date, synced: int) -> None:
    months, _, _ = scrape_window(today, cfg.timezone)
    ny, nm = months[1]
    zone = ZoneInfo(cfg.timezone)
    start = datetime(ny, nm, 1, tzinfo=zone)
    end = datetime(ny + (nm == 12), nm % 12 + 1, 1, tzinfo=zone)
    count, people = store.month_stats(start, end)
    if count and store.mark_published(f"{ny}-{nm:02d}"):
        notify(
            cfg,
            f"📅 {MONTHS_NB[nm - 1].capitalize()} er publisert i RBU: {count} vakter for {people} personer.\n"
            f"{synced} Google-kalendere er oppdatert.",
        )


def _warn_if_session_expiring(cfg: Config) -> None:
    expires = rbu.session_expires_at(cfg.rbu_storage_state)
    if expires and expires - datetime.now(timezone.utc) < SESSION_WARNING:
        day = expires.astimezone(ZoneInfo(cfg.timezone)).strftime("%d.%m.%Y")
        notify(cfg, f"⏳ RBU-innloggingen går ut {day}. Kjør denne på Macen når du har et minutt:\n{cfg.login_command}")


def run(cfg: Config, store: Store, today: date | None = None) -> bool:
    lock_file = open(cfg.db_path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("Another run is in progress")
        return True

    with lock_file:
        today = today or datetime.now(ZoneInfo(cfg.timezone)).date()
        run_id = store.start_run()
        try:
            snap = rbu.fetch_snapshot(cfg, today)
        except rbu.RbuLoginExpired as e:
            store.finish_run(run_id, False, f"RBU-innlogging utløpt: {e}")
            notify(cfg, _login_expired_text(cfg, str(e)))
            return False
        except Exception as e:
            log.exception("RBU fetch failed")
            store.finish_run(run_id, False, f"Henting fra RBU feilet: {e}")
            notify(cfg, f"⚠️ Henting fra RBU feilet:\n{e}\n\nSe {cfg.public_url}/admin")
            return False

        _, window_start, window_end = scrape_window(today, cfg.timezone)
        try:
            store.apply_snapshot(snap, window_start, window_end)
        except SuspiciousSnapshot as e:
            store.finish_run(run_id, False, str(e))
            notify(cfg, f"⚠️ {e}")
            return False

        synced, failed = sync_all(cfg, store)
        _announce_new_month(cfg, store, today, synced)
        _warn_if_session_expiring(cfg)

        teams = ", ".join(name for _, name in snap.teams)
        message = f"{len(snap.shifts)} vakter fra {teams}. {synced} Google-kalendere synket"
        if failed:
            message += f", {failed} feilet"
        store.finish_run(run_id, True, message + ".")
        log.info(message)
        return True
