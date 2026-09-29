"""Talks to RBU through a real (headless) browser.

RBU sits behind Microsoft SSO, which only a browser can pass. We load the app once
so the saved session silently re-authenticates, then call RBU's JSON API with the
same cookies. No page scraping.

RBU's addresses come from config (RBU_APP_URL, RBU_API_URL), not from this code.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, Route, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .config import Config
from .shifts import Snapshot, find_teams, scrape_window

SSO_COOKIE = "ESTSAUTHPERSISTENT"  # Microsoft's "stay signed in" cookie


class RbuLoginExpired(Exception):
    """The saved RBU session no longer works; someone must log in again."""


def _urls(cfg: Config) -> tuple[str, str]:
    """(app, api), or a clear error if the config lacks them."""
    if not (cfg.rbu_app_url and cfg.rbu_api_url):
        raise RuntimeError("RBU_APP_URL og RBU_API_URL mangler i config.env.")
    return cfg.rbu_app_url, cfg.rbu_api_url


def _skip_heavy_assets(route: Route) -> None:
    req = route.request
    if req.resource_type in ("image", "font", "media") or "mouseflow" in req.url:
        route.abort()
    else:
        route.continue_()


def _open_session(page: Page, app: str, api: str, timeout_ms: int) -> None:
    def is_session_call(r) -> bool:
        return r.url.startswith(f"{api}/currentSession")

    try:
        with page.expect_response(is_session_call, timeout=timeout_ms) as info:
            page.goto(app, wait_until="commit", timeout=60_000)
        status = info.value.status
    except PlaywrightTimeout:
        # Anywhere but RBU's own host means the SSO sent us to a login page.
        host = urlsplit(page.url).hostname
        if host and host != urlsplit(app).hostname:
            raise RbuLoginExpired(f"RBU sendte oss til innlogging ({host}).") from None
        raise RuntimeError(f"RBU lastet ikke (endte på {page.url.split('?')[0]}).") from None
    if status in (401, 403):
        raise RbuLoginExpired(f"RBU svarte {status} på sesjonssjekk.")
    if status != 200:
        raise RuntimeError(f"RBU svarte {status} på sesjonssjekk.")


def _get_json(ctx: BrowserContext, api: str, path: str) -> dict:
    r = ctx.request.get(f"{api}{path}", headers={"Accept": "application/hal+json, application/json"}, timeout=60_000)
    if r.status in (401, 403):
        raise RbuLoginExpired(f"RBU API svarte {r.status}.")
    if not r.ok:
        raise RuntimeError(f"RBU API svarte {r.status} på {path.split('?')[0]}.")
    return r.json()


def _save_state(ctx: BrowserContext, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    ctx.storage_state(path=str(tmp))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def fetch_snapshot(cfg: Config, today: date) -> Snapshot:
    """Every team's shifts for the current and next month."""
    app, api = _urls(cfg)
    if not cfg.rbu_storage_state.exists():
        raise RbuLoginExpired(f"Fant ikke {cfg.rbu_storage_state}.")
    months, _, _ = scrape_window(today, cfg.timezone)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=cfg.chromium_path)
        try:
            ctx = browser.new_context(storage_state=str(cfg.rbu_storage_state), timezone_id=cfg.timezone)
            ctx.route("**/*", _skip_heavy_assets)
            _open_session(ctx.new_page(), app, api, timeout_ms=90_000)

            teams = find_teams(_get_json(ctx, api, "/orgunits/structure"))
            if cfg.team_ids:
                teams = [t for t in teams if t[0] in cfg.team_ids]
            if not teams:
                raise RuntimeError("Fant ingen team i RBU.")

            snap = Snapshot(months=months, teams=teams, tz=cfg.timezone)
            for team_id, team_name in teams:
                for year, month in months:
                    payload = _get_json(ctx, api, f"/orgunits/{team_id}/calendar?year={year}&month={month}")
                    snap.add_calendar(payload, team_id, team_name)

            # Keep the refreshed cookies so the session stays warm.
            _save_state(ctx, cfg.rbu_storage_state)
            return snap
        finally:
            browser.close()


def login_interactively(cfg: Config, out_path: Path, timeout_minutes: int = 10) -> None:
    """Open a visible browser, let a human log in, save the session."""
    app, api = _urls(cfg)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=False)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            print("Logg inn på RBU i nettleservinduet. Vinduet lukkes av seg selv når du er inne.")
            with page.expect_response(
                lambda r: r.url.startswith(f"{api}/currentSession") and r.status == 200,
                timeout=timeout_minutes * 60_000,
            ):
                page.goto(app)
            page.wait_for_timeout(2000)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _save_state(ctx, out_path)
        finally:
            browser.close()


def session_expires_at(storage_state: Path) -> datetime | None:
    """When Microsoft's 'stay signed in' cookie runs out, if we can tell."""
    try:
        cookies = json.loads(storage_state.read_text())["cookies"]
    except (OSError, ValueError, KeyError):
        return None
    expiries = [c["expires"] for c in cookies if c.get("name") == SSO_COOKIE and c.get("expires", -1) > 0]
    return datetime.fromtimestamp(max(expiries), timezone.utc) if expiries else None
