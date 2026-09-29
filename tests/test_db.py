from datetime import date, datetime, timezone

import pytest

from vaktkalender.db import CodeTaken, Store, SuspiciousSnapshot
from vaktkalender.shifts import Shift, Snapshot, scrape_window


def shift(mid, code, day, month=10):
    start = datetime(2026, month, day, 10, tzinfo=timezone.utc)
    return Shift(mid, code, f"Vakt {mid}", start, start.replace(hour=14), "READY_TO_START", "Bergen")


def snapshot(*shifts):
    snap = Snapshot(months=[(2026, 10), (2026, 11)])
    for s in shifts:
        snap.shifts[(s.mission_id, s.code)] = s
    return snap


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


WINDOW = scrape_window(date(2026, 10, 1), "Europe/Oslo")[1:]


def test_apply_replaces_window_but_keeps_history(store):
    history = shift(1, "onordmann", 15, month=9)
    store.apply_snapshot(snapshot(history, shift(2, "onordmann", 3), shift(3, "onordmann", 4)),
                         *scrape_window(date(2026, 9, 1), "Europe/Oslo")[1:])

    # A month later: shift 3 was removed in RBU, shift 4 is new.
    store.apply_snapshot(snapshot(shift(2, "onordmann", 3), shift(4, "onordmann", 20)), *WINDOW)

    assert [s.mission_id for s in store.shifts_for("onordmann")] == [1, 2, 4]


def test_empty_scrape_never_wipes_existing_shifts(store):
    store.apply_snapshot(snapshot(shift(2, "onordmann", 3)), *WINDOW)
    with pytest.raises(SuspiciousSnapshot):
        store.apply_snapshot(snapshot(), *WINDOW)
    assert len(store.shifts_for("onordmann")) == 1


def test_a_code_can_only_be_claimed_by_one_email(store):
    first = store.claim("ola@example.com", "onordmann")
    assert store.claim("ola@example.com", "onordmann").ics_token == first.ics_token
    with pytest.raises(CodeTaken):
        store.claim("someone@example.com", "onordmann")


def test_month_is_announced_once(store):
    assert store.mark_published("2026-11")
    assert not store.mark_published("2026-11")


def test_old_database_gets_the_feed_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE members (email TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, "
                  "ics_token TEXT NOT NULL UNIQUE, google_calendar_id TEXT, created_at TEXT NOT NULL, "
                  "last_sync_at TEXT, sync_error TEXT)")
        c.execute("INSERT INTO members VALUES ('ola@example.com', 'onordmann', 't', NULL, '2026-09-25', NULL, NULL)")

    store = Store(path)
    store.record_feed_fetch("ola@example.com", "iOS/18.6 dataaccessd/1.0")

    member = store.member("ola@example.com")
    assert member.feed_fetched_at and member.feed_client == "iOS/18.6 dataaccessd/1.0"
