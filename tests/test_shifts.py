from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from vaktkalender.shifts import Snapshot, find_teams, fold, parse_calendar, scrape_window


def test_parse_calendar_gives_one_shift_per_person_per_mission(calendar_payload):
    employees, shifts = parse_calendar(calendar_payload, 558, "Bergen")

    assert [e.code for e in employees] == ["onordmann", "aostli"]
    assert {(s.mission_id, s.code) for s in shifts} == {
        (101, "onordmann"), (102, "onordmann"), (103, "onordmann"), (101, "aostli")
    }
    finale = next(s for s in shifts if s.mission_id == 101 and s.code == "onordmann")
    assert finale.start == datetime(2026, 10, 3, 13, tzinfo=ZoneInfo("Europe/Oslo"))
    assert finale.coworkers == ("Åse Østli",)
    assert finale.planned_by == "Kari Planlegger"
    assert finale.team_name == "Bergen"


def test_rbu_times_are_norwegian_wall_clock_despite_the_z(calendar_payload):
    # RBU shows Flytrekamp as 10:00-16:00 while its API sends "10:00:00Z".
    payload = {"users": [{"shortName": "onordmann", "missions": [
        {"id": 1, "title": "Flytrekamp", "from": "2026-10-02T10:00:00Z", "to": "2026-10-02T16:00:00Z"},
        {"id": 2, "title": "Vintertid", "from": "2026-11-05T10:00:00Z", "to": "2026-11-05T16:00:00Z"},
    ]}]}
    _, (summer, winter) = parse_calendar(payload, 559, "Trondheim", "Europe/Oslo")

    assert (summer.start, summer.end) == (datetime(2026, 10, 2, 8, tzinfo=timezone.utc),
                                          datetime(2026, 10, 2, 14, tzinfo=timezone.utc))
    assert winter.start == datetime(2026, 11, 5, 9, tzinfo=timezone.utc)


def test_status_decides_active_and_summary(calendar_payload):
    _, shifts = parse_calendar(calendar_payload, 558, "Bergen")
    by_id = {s.mission_id: s for s in shifts if s.code == "onordmann"}

    assert by_id[101].active and by_id[101].summary == "Basement Finale"
    assert by_id[102].active and by_id[102].summary == "Sampling (utkast)"
    assert not by_id[103].active


def test_snapshot_dedupes_across_months(calendar_payload):
    snap = Snapshot(months=[(2026, 10), (2026, 11)])
    snap.add_calendar(calendar_payload, 558, "Bergen")
    snap.add_calendar(calendar_payload, 558, "Bergen")
    assert len(snap.shifts) == 4


def test_find_teams_walks_the_org_tree():
    tree = {
        "id": 2, "name": "Global", "type": "GLOBAL", "children": [
            {"id": 162, "name": "Norway", "type": "COUNTRY", "children": [
                {"id": 558, "name": "Bergen", "type": "TEAM", "active": True,
                 "children": [{"id": 4330, "name": "Campus", "type": "CAMPUS"}]},
                {"id": 999, "name": "Nedlagt", "type": "TEAM", "active": False},
                {"id": 559, "name": "Trondheim", "type": "TEAM", "active": True},
            ]},
        ],
    }
    assert find_teams(tree) == [(558, "Bergen"), (559, "Trondheim")]


def test_fold_forgives_case_spaces_and_norwegian_letters():
    assert fold(" ONordmann ") == fold("onordmann")
    assert fold("aøstli") == fold("aostli")
    assert fold("Åse") == "ase"


def test_scrape_window_spans_current_and_next_month_in_oslo_time():
    months, start, end = scrape_window(date(2026, 12, 15), "Europe/Oslo")
    assert months == [(2026, 12), (2027, 1)]
    assert start == datetime(2026, 11, 30, 23, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 31, 23, tzinfo=timezone.utc)


def test_home_team_is_where_most_shifts_are(calendar_payload):
    snap = Snapshot(months=[(2026, 10)], teams=[(558, "Bergen"), (10957, "Oslo")])
    snap.add_calendar({"users": [{"shortName": "onordmann", "firstName": "Ola", "lastName": "Nordmann",
                                  "missions": []}]}, 10957, "Oslo")  # listed in Oslo, no shifts there
    snap.add_calendar(calendar_payload, 558, "Bergen")
    home = {e.code: e for e in snap.employees_with_home_team()}
    assert (home["onordmann"].team_name, home["onordmann"].team_id) == ("Bergen", 558)
