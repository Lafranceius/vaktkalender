"""SQLite storage shared by the web app and the daily job."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .shifts import Employee, Shift, Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    code TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shifts (
    mission_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    team_name TEXT NOT NULL,
    coworkers TEXT NOT NULL,
    planned_by TEXT,
    PRIMARY KEY (mission_id, code)
);
CREATE INDEX IF NOT EXISTS shifts_by_code ON shifts (code, start_utc);
CREATE INDEX IF NOT EXISTS shifts_by_start ON shifts (start_utc);
CREATE TABLE IF NOT EXISTS members (
    email TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    ics_token TEXT NOT NULL UNIQUE,
    google_calendar_id TEXT,
    created_at TEXT NOT NULL,
    last_sync_at TEXT,
    sync_error TEXT,
    feed_fetched_at TEXT,
    feed_client TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    message TEXT
);
CREATE TABLE IF NOT EXISTS published_months (
    month TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
"""


class CodeTaken(Exception):
    pass


class SuspiciousSnapshot(Exception):
    pass


@dataclass(frozen=True)
class Member:
    email: str
    code: str
    ics_token: str
    google_calendar_id: str | None
    created_at: str
    last_sync_at: str | None
    sync_error: str | None
    feed_fetched_at: str | None = None  # last time a calendar app fetched the ICS feed
    feed_client: str | None = None  # that app's User-Agent


@dataclass(frozen=True)
class Run:
    id: int
    started_at: str
    finished_at: str | None
    ok: bool | None
    message: str | None


def now_iso() -> str:
    return iso(datetime.now(timezone.utc))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _shift(row: sqlite3.Row) -> Shift:
    return Shift(
        mission_id=row["mission_id"],
        code=row["code"],
        title=row["title"],
        start=datetime.fromisoformat(row["start_utc"]),
        end=datetime.fromisoformat(row["end_utc"]),
        status=row["status"],
        team_name=row["team_name"],
        coworkers=tuple(json.loads(row["coworkers"])),
        planned_by=row["planned_by"],
    )


def _employee(row: sqlite3.Row) -> Employee:
    return Employee(row["code"], row["first_name"], row["last_name"], row["team_id"], row["team_name"])


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            columns = {r["name"] for r in c.execute("PRAGMA table_info(members)")}
            for column in ("feed_fetched_at", "feed_client"):  # added after the first deploy
                if column not in columns:
                    c.execute(f"ALTER TABLE members ADD COLUMN {column} TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- scraped data -------------------------------------------------------

    def apply_snapshot(self, snap: Snapshot, window_start: datetime, window_end: datetime) -> None:
        """Replace every shift inside the scraped window with what RBU returned.

        Shifts before the window are history and are never touched.
        """
        ws, we = iso(window_start), iso(window_end)
        with self._conn() as c:
            old = c.execute(
                "SELECT COUNT(*) FROM shifts WHERE start_utc >= ? AND start_utc < ?", (ws, we)
            ).fetchone()[0]
            if old and not snap.shifts:
                raise SuspiciousSnapshot(
                    f"RBU returnerte 0 vakter, men databasen har {old} i perioden. Ingenting ble endret."
                )
            c.execute("DELETE FROM shifts WHERE start_utc >= ? AND start_utc < ?", (ws, we))
            c.executemany(
                "INSERT OR REPLACE INTO shifts VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (s.mission_id, s.code, s.title, iso(s.start), iso(s.end), s.status,
                     s.team_name, json.dumps(list(s.coworkers), ensure_ascii=False), s.planned_by)
                    for s in snap.shifts.values()
                ],
            )
            seen = now_iso()
            c.executemany(
                "INSERT INTO employees VALUES (?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "first_name=excluded.first_name, last_name=excluded.last_name, team_id=excluded.team_id, "
                "team_name=excluded.team_name, last_seen_at=excluded.last_seen_at",
                [(e.code, e.first_name, e.last_name, e.team_id, e.team_name, seen) for e in snap.employees_with_home_team()],
            )

    def shifts_for(self, code: str, since: datetime | None = None, until: datetime | None = None) -> list[Shift]:
        sql, args = "SELECT * FROM shifts WHERE code = ?", [code]
        if since:
            sql, args = sql + " AND end_utc >= ?", args + [iso(since)]
        if until:
            sql, args = sql + " AND start_utc < ?", args + [iso(until)]
        with self._conn() as c:
            return [_shift(r) for r in c.execute(sql + " ORDER BY start_utc", args)]

    def month_stats(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(active shifts, distinct people) starting in [start, end)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*), COUNT(DISTINCT code) FROM shifts WHERE start_utc >= ? AND start_utc < ? "
                "AND status NOT IN ('CANCELED','DELETED')",
                (iso(start), iso(end)),
            ).fetchone()
            return row[0], row[1]

    def employee(self, code: str) -> Employee | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM employees WHERE code = ?", (code,)).fetchone()
            return _employee(row) if row else None

    def employees(self) -> list[Employee]:
        with self._conn() as c:
            return [_employee(r) for r in c.execute("SELECT * FROM employees ORDER BY team_name, first_name")]

    # --- members (colleagues who have logged in and claimed a code) ---------

    def member(self, email: str) -> Member | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM members WHERE email = ?", (email,)).fetchone()
            return Member(**dict(row)) if row else None

    def member_by_token(self, token: str) -> Member | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM members WHERE ics_token = ?", (token,)).fetchone()
            return Member(**dict(row)) if row else None

    def members(self) -> list[Member]:
        with self._conn() as c:
            return [Member(**dict(r)) for r in c.execute("SELECT * FROM members ORDER BY created_at")]

    def claim(self, email: str, code: str) -> Member:
        with self._conn() as c:
            owner = c.execute("SELECT email FROM members WHERE code = ?", (code,)).fetchone()
            if owner and owner["email"] != email:
                raise CodeTaken(code)
            c.execute(
                "INSERT INTO members (email, code, ics_token, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET code=excluded.code",
                (email, code, secrets.token_urlsafe(24), now_iso()),
            )
        member = self.member(email)
        assert member
        return member

    def set_google_calendar(self, email: str, calendar_id: str | None) -> None:
        with self._conn() as c:
            c.execute("UPDATE members SET google_calendar_id = ? WHERE email = ?", (calendar_id, email))

    def record_sync(self, email: str, error: str | None) -> None:
        with self._conn() as c:
            if error:
                c.execute("UPDATE members SET sync_error = ? WHERE email = ?", (error, email))
            else:
                c.execute("UPDATE members SET sync_error = NULL, last_sync_at = ? WHERE email = ?", (now_iso(), email))

    def record_feed_fetch(self, email: str, client: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE members SET feed_fetched_at = ?, feed_client = ? WHERE email = ?",
                      (now_iso(), client[:200], email))

    def delete_member(self, email: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM members WHERE email = ?", (email,))

    # --- job bookkeeping ----------------------------------------------------

    def start_run(self) -> int:
        with self._conn() as c:
            return c.execute("INSERT INTO runs (started_at) VALUES (?)", (now_iso(),)).lastrowid

    def finish_run(self, run_id: int, ok: bool, message: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET finished_at = ?, ok = ?, message = ? WHERE id = ?",
                (now_iso(), int(ok), message, run_id),
            )

    def runs(self, limit: int = 10) -> list[Run]:
        with self._conn() as c:
            return [
                Run(r["id"], r["started_at"], r["finished_at"], None if r["ok"] is None else bool(r["ok"]), r["message"])
                for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def last_successful_run(self) -> Run | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE ok = 1 ORDER BY id DESC LIMIT 1").fetchone()
            return Run(r["id"], r["started_at"], r["finished_at"], True, r["message"]) if r else None

    def mark_published(self, month: str) -> bool:
        """True the first time a month is seen with shifts in it."""
        with self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO published_months VALUES (?, ?)", (month, now_iso()))
            return cur.rowcount == 1
