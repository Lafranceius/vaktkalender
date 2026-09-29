"""Domain types and pure parsing of RBU's JSON API responses."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

# Missions in these states are not shifts anyone should show up for.
INACTIVE_STATUSES = frozenset({"CANCELED", "DELETED"})

STATUS_LABELS = {
    "DRAFT": "Utkast",
    "PLANNED": "Planlagt",
    "NEED_MORE_INFORMATION": "Trenger mer info",
    "READY_TO_START": "Klar",
    "REPORT_SENT": "Rapport sendt",
    "REPORT_INCOMPLETE": "Rapport ufullstendig",
    "COMPLETED": "Fullført",
    "CANCELED": "Avlyst",
    "DELETED": "Slettet",
}


@dataclass(frozen=True)
class Employee:
    code: str
    first_name: str
    last_name: str
    team_id: int
    team_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(frozen=True)
class Shift:
    """One employee's assignment to one RBU mission."""

    mission_id: int
    code: str
    title: str
    start: datetime  # aware, UTC
    end: datetime  # aware, UTC
    status: str
    team_name: str
    coworkers: tuple[str, ...] = ()
    planned_by: str | None = None

    @property
    def active(self) -> bool:
        return self.status not in INACTIVE_STATUSES

    @property
    def summary(self) -> str:
        return f"{self.title} (utkast)" if self.status == "DRAFT" else self.title

    @property
    def description(self) -> str:
        lines = [f"Team: {self.team_name}"]
        if self.coworkers:
            lines.append("Sammen med: " + ", ".join(self.coworkers))
        if self.planned_by:
            lines.append(f"Planlagt av: {self.planned_by}")
        lines.append(f"Status: {STATUS_LABELS.get(self.status, self.status)}")
        lines.append("")
        lines.append("Automatisk fra RBU – endringer gjøres i RBU.")
        return "\n".join(lines)


@dataclass
class Snapshot:
    """Everything one scrape saw, across all teams and months."""

    months: list[tuple[int, int]]
    teams: list[tuple[int, str]] = field(default_factory=list)
    tz: str = "Europe/Oslo"  # the zone RBU's times are really in
    employees: dict[str, Employee] = field(default_factory=dict)
    shifts: dict[tuple[int, str], Shift] = field(default_factory=dict)

    def add_calendar(self, payload: dict, team_id: int, team_name: str) -> None:
        employees, shifts = parse_calendar(payload, team_id, team_name, self.tz)
        for e in employees:
            self.employees.setdefault(e.code, e)
        for s in shifts:
            self.shifts[(s.mission_id, s.code)] = s

    def employees_with_home_team(self) -> list[Employee]:
        """People can show up in several team calendars; their team is where most of their shifts are."""
        team_ids = {name: tid for tid, name in self.teams}
        counts: dict[str, Counter[str]] = {}
        for s in self.shifts.values():
            counts.setdefault(s.code, Counter())[s.team_name] += 1
        result = []
        for e in self.employees.values():
            if e.code in counts:
                home = counts[e.code].most_common(1)[0][0]
                e = replace(e, team_name=home, team_id=team_ids.get(home, e.team_id))
            result.append(e)
        return result


def _parse_time(value: str, tz: ZoneInfo) -> datetime:
    """RBU marks local wall-clock time as UTC: "10:00:00Z" is 10:00 in Norway, as RBU itself shows it."""
    return datetime.fromisoformat(value).replace(tzinfo=tz).astimezone(timezone.utc)


def _name(person: dict | None) -> str | None:
    if not person:
        return None
    name = f"{person.get('firstName', '')} {person.get('lastName', '')}".strip()
    return name or person.get("shortName")


def normalize_code(code: str) -> str:
    return code.strip().lower()


def parse_calendar(payload: dict, team_id: int, team_name: str,
                   tz: str = "Europe/Oslo") -> tuple[list[Employee], list[Shift]]:
    """Parse GET /api/orgunits/{team}/calendar?year=&month=."""
    zone = ZoneInfo(tz)
    employees: list[Employee] = []
    shifts: list[Shift] = []
    for user in payload.get("users", []):
        code = normalize_code(user.get("shortName") or "")
        if not code:
            continue
        employees.append(
            Employee(code, user.get("firstName", ""), user.get("lastName", ""), team_id, team_name)
        )
        for m in user.get("missions", []):
            coworkers = tuple(
                n
                for sm in m.get("studentMarketeers", [])
                if normalize_code(sm.get("shortName") or "") != code and (n := _name(sm))
            )
            shifts.append(
                Shift(
                    mission_id=int(m["id"]),
                    code=code,
                    title=(m.get("title") or "Vakt").strip(),
                    start=_parse_time(m["from"], zone),
                    end=_parse_time(m["to"], zone),
                    status=m.get("status", ""),
                    team_name=(m.get("team") or {}).get("name") or team_name,
                    coworkers=coworkers,
                    planned_by=_name(m.get("plannedBy")),
                )
            )
    return employees, shifts


def find_teams(structure: dict) -> list[tuple[int, str]]:
    """All active TEAM org units in GET /api/orgunits/structure."""
    teams: list[tuple[int, str]] = []

    def walk(node: dict) -> None:
        if node.get("type") == "TEAM" and node.get("active", True):
            teams.append((int(node["id"]), node["name"]))
        for child in node.get("children") or []:
            walk(child)

    walk(structure)
    return teams


def fold(text: str) -> str:
    """Loose match key so 'Ø' vs 'o', case and stray spaces don't matter."""
    text = text.strip().lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


def scrape_window(today: date, tz: str) -> tuple[list[tuple[int, int]], datetime, datetime]:
    """Current and next month, and the UTC span they cover."""
    y, m = today.year, today.month
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    ay, am = (ny + 1, 1) if nm == 12 else (ny, nm + 1)
    zone = ZoneInfo(tz)
    start = datetime.combine(date(y, m, 1), time(0), zone).astimezone(timezone.utc)
    end = datetime.combine(date(ay, am, 1), time(0), zone).astimezone(timezone.utc)
    return [(y, m), (ny, nm)], start, end
