"""The website colleagues visit once, plus the ICS feed and the admin page."""

from __future__ import annotations

import calendar
import hashlib
import logging
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, ics, rbu
from .access import AccessVerifier, NotAuthenticated
from .db import CodeTaken, Member, Store
from .gcal import CALENDAR_NAME, GoogleCalendar, add_to_google_url
from .job import sync_member
from .shifts import Shift, fold

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
WEEKDAYS = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]
MONTHS_FULL = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
               "september", "oktober", "november", "desember"]
DECORATIVE_DAYS = {2, 6, 9, 13, 17, 20, 24, 27, 30}
# iPhone browsers that don't hand webcal:// links to Calendar; only Safari does.
IOS_BROWSERS = {"CriOS": "Chrome", "FxiOS": "Firefox", "EdgiOS": "Edge", "GSA/": "Google-appen",
                "FBAN": "Facebook/Messenger", "FBAV": "Facebook/Messenger", "Instagram": "Instagram",
                "Snapchat": "Snapchat", "LinkedInApp": "LinkedIn"}
FEED_CLIENTS = {"iOS/": "iPhone/iPad", "dataaccessd": "iPhone/iPad", "macOS/": "Mac", "CalendarAgent": "Mac",
                "Google": "Google", "Microsoft": "Outlook"}


def browser_without_webcal(user_agent: str) -> str | None:
    """The iPhone browser the page is open in, if the Apple button can't work there."""
    if "iPhone" not in user_agent and "iPad" not in user_agent:
        return None
    for token, name in IOS_BROWSERS.items():
        if token in user_agent:
            return name
    return None if "Safari/" in user_agent else "en app"  # in-app browsers leave out "Safari/"


def feed_client(user_agent: str | None) -> str:
    """Short name for the calendar app that fetched a feed."""
    if not user_agent:
        return ""
    return next((name for token, name in FEED_CLIENTS.items() if token in user_agent), user_agent[:30])


def create_app(cfg: config.Config | None = None) -> FastAPI:
    cfg = cfg or config.load()
    store = Store(cfg.db_path)
    verifier = AccessVerifier(cfg)
    zone = ZoneInfo(cfg.timezone)
    templates = Jinja2Templates(directory=HERE / "templates")

    def when(shift: Shift) -> str:
        s, e = shift.start.astimezone(zone), shift.end.astimezone(zone)
        return f"{WEEKDAYS[s.weekday()]} {s.day}. {MONTHS[s.month - 1]} · {s:%H:%M}–{e:%H:%M}"

    def local_time(value: str | datetime | None) -> str:
        if not value:
            return "aldri"
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
        return dt.astimezone(zone).strftime("%d.%m.%Y kl. %H:%M")

    def shift_date(shift: Shift) -> dict:
        s, e = shift.start.astimezone(zone), shift.end.astimezone(zone)
        return {"wd": WEEKDAYS[s.weekday()], "day": s.day, "mon": MONTHS[s.month - 1],
                "time": f"{s:%H:%M}–{e:%H:%M}"}

    def background_calendar(shifts: list[Shift] | None = None) -> dict:
        """This month as a big decorative grid; the viewer's shift days light up."""
        today = datetime.now(zone).date()
        shift_days = {
            s.start.astimezone(zone).day
            for s in shifts or []
            if s.active and (s.start.astimezone(zone).year, s.start.astimezone(zone).month) == (today.year, today.month)
        }
        cells: list[dict] = [{"day": None, "cls": "is-empty", "label": ""}] * today.replace(day=1).weekday()
        for day in range(1, calendar.monthrange(today.year, today.month)[1] + 1):
            if day == today.day:
                cell = {"cls": "is-today", "label": "I DAG"}
            elif day in shift_days:
                cell = {"cls": f"is-shift c{day % 6}", "label": "VAKT"}
            elif not shifts and day in DECORATIVE_DAYS:
                cell = {"cls": f"is-shift c{day % 6}", "label": ""}
            else:
                cell = {"cls": "", "label": ""}
            cells.append({"day": day, **cell})
        cells += [{"day": None, "cls": "is-empty", "label": ""}] * (-len(cells) % 7)
        return {"title": f"{MONTHS_FULL[today.month - 1]} {today.year}", "weekdays": WEEKDAYS, "cells": cells}

    # Versioned asset URLs, so Cloudflare and browsers never serve a stale stylesheet after a deploy.
    versions = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:10] for f in (HERE / "static").iterdir() if f.is_file()}

    def static(name: str) -> str:
        return f"/static/{name}?v={versions.get(name, '0')}"

    templates.env.globals.update(when=when, local_time=local_time, contact=cfg.contact_name,
                                 shift_date=shift_date, background_calendar=background_calendar, static=static,
                                 feed_client=feed_client)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def google() -> GoogleCalendar:
        if not cfg.google_enabled:
            raise HTTPException(503, "Google er ikke satt opp ennå.")
        return GoogleCalendar(cfg.google_service_account_file)

    # --- request guards -----------------------------------------------------

    def current_email(request: Request) -> str:
        token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
        try:
            return verifier.email(token)
        except NotAuthenticated as e:
            log.warning("access denied: %s", e)
            raise HTTPException(403, "Ingen tilgang.") from None

    def same_origin(request: Request) -> None:
        """Reject cross-site form posts."""
        source = request.headers.get("origin") or request.headers.get("referer") or ""
        allowed = {cfg.public_host}
        if cfg.dev_user_email:
            allowed.add(request.url.netloc)
        if urlsplit(source).netloc not in allowed:
            raise HTTPException(403, "Ugyldig opprinnelse.")

    def current_admin(email: str = Depends(current_email)) -> str:
        if email not in cfg.admin_emails:
            raise HTTPException(403, "Kun for administrator.")
        return email

    def current_member(email: str = Depends(current_email)) -> Member:
        member = store.member(email)
        if not member:
            raise HTTPException(303, headers={"Location": "/"})
        return member

    # --- pages --------------------------------------------------------------

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def upcoming(code: str, limit: int | None = None) -> list[Shift]:
        shifts = [s for s in store.shifts_for(code, since=datetime.now(timezone.utc)) if s.active]
        return shifts[:limit] if limit else shifts

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, email: str = Depends(current_email)):
        member = store.member(email)
        if not member:
            return page(request, "start.html", email=email, has_data=bool(store.last_successful_run()))
        base = cfg.public_url
        feed = f"{base}/ics/{member.ics_token}.ics"
        return page(
            request,
            "home.html",
            email=email,
            member=member,
            employee=store.employee(member.code),
            shifts=upcoming(member.code, limit=8),
            bg_shifts=store.shifts_for(member.code),
            google_url=add_to_google_url(member.google_calendar_id) if member.google_calendar_id else None,
            google_enabled=cfg.google_enabled,
            webcal_url="webcal://" + feed.split("://", 1)[1],
            feed_url=feed,
            other_browser=browser_without_webcal(request.headers.get("user-agent", "")),
            last_run=store.last_successful_run(),
            is_admin=email in cfg.admin_emails,
        )

    @app.post("/kode", response_class=HTMLResponse, dependencies=[Depends(same_origin)])
    def find_code(request: Request, code: str = Form(...), email: str = Depends(current_email)):
        matches = [e for e in store.employees() if fold(e.code) == fold(code)]
        if len(matches) != 1:
            return page(request, "start.html", status=422, email=email, code=code,
                        has_data=bool(store.last_successful_run()), not_found=not matches)
        employee = matches[0]
        return page(request, "confirm.html", employee=employee, shifts=upcoming(employee.code, limit=5),
                    bg_shifts=store.shifts_for(employee.code))

    @app.post("/koble", dependencies=[Depends(same_origin)])
    def claim(request: Request, code: str = Form(...), email: str = Depends(current_email)):
        if not store.employee(code):
            raise HTTPException(400, "Ukjent kode.")
        try:
            store.claim(email, code)
        except CodeTaken:
            return page(request, "start.html", status=409, email=email, code=code, taken=True, has_data=True)
        return RedirectResponse("/", status_code=303)

    new_calendar = threading.Lock()  # a double tap must not make a second calendar

    def first_sync(client: GoogleCalendar, email: str) -> None:
        try:
            sync_member(cfg, store, client, store.member(email))
        except Exception:
            log.exception("first sync failed")  # the nightly run retries

    @app.post("/google", dependencies=[Depends(same_origin)])
    def connect_google(request: Request, background: BackgroundTasks, member: Member = Depends(current_member)):
        client = google()
        with new_calendar:
            calendar_id = store.member(member.email).google_calendar_id
            if calendar_id:
                return RedirectResponse(add_to_google_url(calendar_id), status_code=303)
            calendar_id = client.create_calendar(cfg.timezone)
            try:
                client.share_read_only(calendar_id, member.email)
            except Exception:
                client.delete_calendar(calendar_id)
                log.exception("sharing failed")
                return page(request, "error.html", status=502, message=(
                    f"Google godtok ikke {member.email} som Google-konto. "
                    f"Bruk Apple-knappen, eller be {cfg.contact_name} legge inn Google-adressen din."))
            store.set_google_calendar(member.email, calendar_id)
        # Filling it can take minutes when Google throttles; answer now so nobody taps again.
        background.add_task(first_sync, client, member.email)
        return RedirectResponse(add_to_google_url(calendar_id), status_code=303)

    @app.post("/koble-fra", dependencies=[Depends(same_origin)])
    def disconnect(member: Member = Depends(current_member)):
        if member.google_calendar_id:
            google().delete_calendar(member.google_calendar_id)
        store.delete_member(member.email)
        return RedirectResponse("/", status_code=303)

    # --- calendar feed (Cloudflare Access bypasses /ics/; the token is the key) ---

    @app.api_route("/ics/{token}.ics", methods=["GET", "HEAD"])  # some calendar apps check with HEAD first
    def feed(token: str, request: Request):
        member = store.member_by_token(token)
        if not member:
            raise HTTPException(404)
        if request.method == "GET":
            store.record_feed_fetch(member.email, request.headers.get("user-agent", ""))
        body = ics.build(store.shifts_for(member.code), CALENDAR_NAME, cfg.timezone)
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

    # --- admin --------------------------------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, started: int = 0, email: str = Depends(current_admin)):
        now = datetime.now(timezone.utc)
        members = store.members()
        claimed = {m.code for m in members}
        employees = {e.code: e for e in store.employees()}
        rows = [
            (m, employees.get(m.code), len([s for s in store.shifts_for(m.code, since=now) if s.active]))
            for m in members
        ]
        return page(
            request,
            "admin.html",
            rows=rows,
            unclaimed=[e for code, e in employees.items() if code not in claimed],
            runs=store.runs(10),
            session_expires=rbu.session_expires_at(cfg.rbu_storage_state),
            started=started,
        )

    @app.post("/admin/scrape", dependencies=[Depends(same_origin)])
    def scrape_now(email: str = Depends(current_admin)):
        subprocess.Popen([sys.executable, "-m", "vaktkalender", "run"], start_new_session=True)
        return RedirectResponse("/admin?started=1", status_code=303)

    @app.post("/admin/fjern", dependencies=[Depends(same_origin)])
    def remove_member(target: str = Form(...), email: str = Depends(current_admin)):
        member = store.member(target)
        if member:
            if member.google_calendar_id:
                google().delete_calendar(member.google_calendar_id)
            store.delete_member(member.email)
        return RedirectResponse("/admin", status_code=303)

    @app.get("/healthz", response_class=PlainTextResponse)
    def health():
        return "ok"

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        return page(request, "error.html", status=exc.status_code, message=exc.detail)

    return app
