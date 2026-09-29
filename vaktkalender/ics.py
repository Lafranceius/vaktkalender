"""iCalendar feed for Apple Calendar (and anything else that subscribes to a URL)."""

from __future__ import annotations

from datetime import datetime, timezone

from .shifts import Shift


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(shifts: list[Shift], calendar_name: str, tz: str, now: datetime | None = None) -> str:
    stamp = _utc(now or datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//vaktkalender//RBU//NO",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-TIMEZONE:{tz}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for s in shifts:
        if not s.active:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:rbu-{s.mission_id}-{s.code}@vaktkalender",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(s.start)}",
            f"DTEND:{_utc(s.end)}",
            f"SUMMARY:{_escape(s.summary)}",
            f"DESCRIPTION:{_escape(s.description)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
