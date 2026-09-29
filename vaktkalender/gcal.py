"""Google Calendar through a service account.

The service account owns one "Red Bull-vakter" calendar per colleague and shares
it read-only with them. Each calendar mirrors that colleague's shifts in the
database exactly; nothing else ever lives in it.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from .shifts import Shift

SCOPES = ["https://www.googleapis.com/auth/calendar"]
BASE = "https://www.googleapis.com/calendar/v3"
CALENDAR_NAME = "Red Bull-vakter"
EVENT_PREFIX = "rbu"  # Google event ids must be base32hex: 0-9 and a-v
RETRIES = 8  # waits 1+2+4+…+64+64 s, about 3 minutes, before giving up on one request


class GoogleError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Google svarte {status}: {detail}")
        self.status = status


def event_id(shift: Shift) -> str:
    return f"{EVENT_PREFIX}{shift.mission_id}"


def event_body(shift: Shift, tz: str) -> dict:
    zone = ZoneInfo(tz)
    body = {
        "id": event_id(shift),
        "status": "confirmed",
        "summary": shift.summary,
        "description": shift.description,
        "start": {"dateTime": shift.start.astimezone(zone).isoformat(), "timeZone": tz},
        "end": {"dateTime": shift.end.astimezone(zone).isoformat(), "timeZone": tz},
    }
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    body["extendedProperties"] = {"private": {"fp": fingerprint}}
    return body


@dataclass
class Plan:
    inserts: list[dict]
    updates: list[dict]
    deletes: list[str]


def plan(desired: list[Shift], existing: dict[str, dict], tz: str) -> Plan:
    """What to change so the calendar matches `desired` exactly."""
    bodies = {event_id(s): event_body(s, tz) for s in desired if s.active}
    changed = [
        body
        for eid, body in bodies.items()
        if existing.get(eid, {}).get("extendedProperties", {}).get("private", {}).get("fp")
        != body["extendedProperties"]["private"]["fp"]
    ]
    deletes = [eid for eid in existing if eid not in bodies and eid.startswith(EVENT_PREFIX)]
    return Plan(
        inserts=[b for b in changed if b["id"] not in existing],
        updates=[b for b in changed if b["id"] in existing],
        deletes=deletes,
    )


def add_to_google_url(calendar_id: str) -> str:
    return f"https://calendar.google.com/calendar/u/0/r?cid={quote(calendar_id)}"


class GoogleCalendar:
    def __init__(self, key_file: Path):
        creds = service_account.Credentials.from_service_account_file(str(key_file), scopes=SCOPES)
        self._http = AuthorizedSession(creds)

    def _call(self, method: str, path: str, ok_missing: bool = False, **kw) -> dict | None:
        for attempt in range(RETRIES + 1):
            r = self._http.request(method, BASE + path, timeout=30, **kw)
            rate_limited = r.status_code == 429 or (r.status_code == 403 and "ateLimitExceeded" in r.text)
            if not (rate_limited or r.status_code >= 500) or attempt == RETRIES:
                break
            # Google's advice: exponential backoff with jitter, capped at about a minute.
            time.sleep(min(2**attempt, 64) + random.random())
        if ok_missing and r.status_code in (404, 410):
            return None
        if not r.ok:
            raise GoogleError(r.status_code, r.text[:300])
        return r.json() if r.content else None

    @staticmethod
    def _cal(calendar_id: str) -> str:
        return f"/calendars/{quote(calendar_id, safe='')}"

    def create_calendar(self, tz: str) -> str:
        cal = self._call("POST", "/calendars", json={"summary": CALENDAR_NAME, "timeZone": tz})
        return cal["id"]

    def share_read_only(self, calendar_id: str, email: str) -> None:
        self._call(
            "POST",
            f"{self._cal(calendar_id)}/acl",
            params={"sendNotifications": "true"},  # Google also emails an "add calendar" link
            json={"role": "reader", "scope": {"type": "user", "value": email}},
        )

    def list_calendars(self) -> list[dict]:
        return self._call("GET", "/users/me/calendarList", params={"maxResults": "250"}).get("items", [])

    def readers(self, calendar_id: str) -> list[str]:
        acl = self._call("GET", f"{self._cal(calendar_id)}/acl").get("items", [])
        return [rule["scope"].get("value", "") for rule in acl if rule["role"] == "reader"]

    def delete_calendar(self, calendar_id: str) -> None:
        self._call("DELETE", self._cal(calendar_id), ok_missing=True)

    def list_events(self, calendar_id: str) -> dict[str, dict]:
        events: dict[str, dict] = {}
        params = {"maxResults": "2500", "showDeleted": "false"}
        while True:
            page = self._call("GET", f"{self._cal(calendar_id)}/events", params=params)
            for ev in page.get("items", []):
                events[ev["id"]] = ev
            if not page.get("nextPageToken"):
                return events
            params["pageToken"] = page["nextPageToken"]

    def insert_event(self, calendar_id: str, body: dict) -> None:
        try:
            self._call("POST", f"{self._cal(calendar_id)}/events", json=body)
        except GoogleError as e:
            if e.status != 409:  # 409: the id exists (deleted earlier) — overwrite it
                raise
            self.update_event(calendar_id, body)

    def update_event(self, calendar_id: str, body: dict) -> None:
        self._call("PUT", f"{self._cal(calendar_id)}/events/{body['id']}", json=body)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._call("DELETE", f"{self._cal(calendar_id)}/events/{event_id}", ok_missing=True)

    def mirror(self, calendar_id: str, shifts: list[Shift], tz: str) -> Plan:
        todo = plan(shifts, self.list_events(calendar_id), tz)
        for body in todo.updates:
            self.update_event(calendar_id, body)
        for body in todo.inserts:
            self.insert_event(calendar_id, body)
        for eid in todo.deletes:
            self.delete_event(calendar_id, eid)
        return todo
