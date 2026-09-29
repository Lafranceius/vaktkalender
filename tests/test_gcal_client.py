import json

import pytest

from vaktkalender import gcal
from vaktkalender.gcal import GoogleCalendar, event_body
from tests.test_calendars import TZ, shift

RATE_LIMITED = json.dumps({"error": {"errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded"}], "code": 403}})


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body or {})
        self.content = self.text.encode()
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Answers with queued responses, then 200 {}; remembers every request."""

    def __init__(self, *queued):
        self.queued = list(queued)
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url.removeprefix(gcal.BASE)))
        if self.queued:
            return self.queued.pop(0)
        if method == "GET":
            return Response(200, {"items": []})
        return Response(200, {})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(gcal.time, "sleep", lambda s: None)


def client(http):
    c = object.__new__(GoogleCalendar)
    c._http = http
    return c


def test_existing_events_are_updated_in_place_and_new_ones_inserted():
    existing = event_body(shift(1, hour=9), TZ)
    http = FakeHttp(Response(200, {"items": [existing]}))
    client(http).mirror("cal", [shift(1), shift(2)], TZ)
    assert http.requests[1:] == [("PUT", "/calendars/cal/events/rbu1"), ("POST", "/calendars/cal/events")]


def test_rate_limits_are_waited_out():
    http = FakeHttp(*[Response(403, RATE_LIMITED)] * 7)
    client(http).insert_event("cal", event_body(shift(1), TZ))
    assert len(http.requests) == 8


def test_gives_up_when_google_keeps_refusing():
    http = FakeHttp(*[Response(403, RATE_LIMITED)] * 50)
    with pytest.raises(gcal.GoogleError):
        client(http).insert_event("cal", event_body(shift(1), TZ))
