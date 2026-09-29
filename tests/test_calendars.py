from datetime import datetime, timezone

from vaktkalender import ics
from vaktkalender.gcal import event_body, plan
from vaktkalender.shifts import Shift

TZ = "Europe/Oslo"


def shift(mid, title="Basement Finale", status="READY_TO_START", hour=13):
    start = datetime(2026, 10, 3, hour, tzinfo=timezone.utc)
    return Shift(mid, "onordmann", title, start, start.replace(hour=hour + 7), status, "Bergen",
                 coworkers=("Åse Østli",), planned_by="Kari Planlegger")


def test_event_body_uses_local_time_and_stable_id():
    body = event_body(shift(101), TZ)
    assert body["id"] == "rbu101"
    assert body["start"] == {"dateTime": "2026-10-03T15:00:00+02:00", "timeZone": TZ}
    assert "Sammen med: Åse Østli" in body["description"]


def test_plan_only_touches_what_changed():
    unchanged, moved, new, gone = shift(1), shift(2), shift(3), shift(4)
    existing = {
        "rbu1": event_body(unchanged, TZ),
        "rbu2": event_body(shift(2, hour=9), TZ),
        "rbu4": event_body(gone, TZ),
    }
    todo = plan([unchanged, moved, new], existing, TZ)
    assert [b["id"] for b in todo.updates] == ["rbu2"]
    assert [b["id"] for b in todo.inserts] == ["rbu3"]
    assert todo.deletes == ["rbu4"]


def test_plan_removes_cancelled_shifts():
    existing = {"rbu5": event_body(shift(5), TZ)}
    todo = plan([shift(5, status="CANCELED")], existing, TZ)
    assert todo.inserts == todo.updates == [] and todo.deletes == ["rbu5"]


def test_ics_escapes_folds_and_skips_cancelled():
    long_title = "Sampling, Bergen; " + "x" * 80
    body = ics.build([shift(1, title=long_title), shift(2, status="CANCELED")], "Red Bull-vakter", TZ,
                     now=datetime(2026, 9, 1, tzinfo=timezone.utc))

    assert body.count("BEGIN:VEVENT") == 1
    assert "UID:rbu-1-onordmann@vaktkalender" in body
    assert "DTSTART:20261003T130000Z" in body
    assert "SUMMARY:Sampling\\, Bergen\\; " in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert body.endswith("END:VCALENDAR\r\n")
