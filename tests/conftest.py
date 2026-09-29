"""Fictional RBU data only — no real names or codes."""

from __future__ import annotations

import pytest


def mission(mid, title, start, end, status="READY_TO_START", team=("Bergen", 558), people=()):
    return {
        "id": mid,
        "title": title,
        "from": start,
        "to": end,
        "status": status,
        "studentMarketeers": [{"id": i, "shortName": c, "firstName": f, "lastName": l} for i, (c, f, l) in enumerate(people)],
        "plannedBy": {"shortName": "kplanlegger", "firstName": "Kari", "lastName": "Planlegger"},
        "team": {"id": team[1], "name": team[0]},
    }


OLA = ("onordmann", "Ola", "Nordmann")
ASE = ("aostli", "Åse", "Østli")


@pytest.fixture
def calendar_payload():
    return {
        "users": [
            {
                "shortName": "onordmann", "firstName": "Ola", "lastName": "Nordmann",
                "missions": [
                    mission(101, "Basement Finale", "2026-10-03T13:00:00Z", "2026-10-03T20:00:00Z", people=[OLA, ASE]),
                    mission(102, "Sampling", "2026-10-10T08:00:00Z", "2026-10-10T12:00:00Z", status="DRAFT", people=[OLA]),
                    mission(103, "Avlyst ting", "2026-10-12T08:00:00Z", "2026-10-12T12:00:00Z", status="CANCELED", people=[OLA]),
                ],
                "unavailabilities": [], "personalEvents": [],
            },
            {
                "shortName": "aostli", "firstName": "Åse", "lastName": "Østli",
                "missions": [
                    mission(101, "Basement Finale", "2026-10-03T13:00:00Z", "2026-10-03T20:00:00Z", people=[OLA, ASE]),
                ],
                "unavailabilities": [], "personalEvents": [],
            },
        ]
    }
