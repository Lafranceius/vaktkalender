import dataclasses
import time
from datetime import date

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from vaktkalender import config
from vaktkalender.access import AccessVerifier, NotAuthenticated
from vaktkalender.db import Store
from vaktkalender.shifts import Snapshot, scrape_window
from vaktkalender.web import create_app

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VAKT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("RBU_STORAGE_STATE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DEV_USER_EMAIL", "ola@example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    return config.load()


@pytest.fixture
def client(cfg, calendar_payload):
    store = Store(cfg.db_path)
    snap = Snapshot(months=[(2026, 10), (2026, 11)])
    snap.add_calendar(calendar_payload, 558, "Bergen")
    store.apply_snapshot(snap, *scrape_window(date(2026, 10, 1), cfg.timezone)[1:])
    store.finish_run(store.start_run(), True, "test")
    return TestClient(create_app(cfg))


def test_colleague_finds_code_confirms_and_gets_feed(client):
    assert "Skriv koden din" in client.get("/").text

    found = client.post("/kode", data={"code": " ONordmann "}, headers=ORIGIN)
    assert "Ola Nordmann" in found.text

    home = client.post("/koble", data={"code": "onordmann"}, headers=ORIGIN)
    assert "Hei Ola!" in home.text
    assert "webcal://" in home.text

    token = home.text.split("/ics/")[1].split(".ics")[0]
    feed = client.get(f"/ics/{token}.ics")
    assert feed.headers["content-type"].startswith("text/calendar")
    assert feed.text.count("BEGIN:VEVENT") == 2  # the cancelled one is left out


def test_unknown_code_explains_the_format(client):
    r = client.post("/kode", data={"code": "finnesikke"}, headers=ORIGIN)
    assert r.status_code == 422
    assert "Fant ingen" in r.text


def test_cross_site_posts_are_rejected(client):
    r = client.post("/kode", data={"code": "onordmann"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_feed_needs_a_valid_token(client):
    assert client.get("/ics/nope.ics").status_code == 404


def test_admin_is_only_for_admins(client):
    assert client.get("/admin").status_code == 403


def test_access_verifier_checks_signature_and_audience(cfg, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = dataclasses.replace(cfg, dev_user_email=None, cf_team_domain="team.cloudflareaccess.com", cf_audience="aud1")
    verifier = AccessVerifier(cfg)
    monkeypatch.setattr(verifier._jwks, "get_signing_key_from_jwt",
                        lambda token: type("K", (), {"key": key.public_key()}))

    def token(**claims):
        base = {"email": "Ola@Example.com", "aud": "aud1", "iss": "https://team.cloudflareaccess.com",
                "exp": int(time.time()) + 60}
        return jwt.encode(base | claims, key, algorithm="RS256")

    assert verifier.email(token()) == "ola@example.com"
    for bad in (token(aud="other"), token(iss="https://evil"), token(exp=1), None):
        with pytest.raises(NotAuthenticated):
            verifier.email(bad)


class FakeGoogle:
    def __init__(self):
        self.calendars = []
        self.mirrored = []

    def create_calendar(self, tz):
        time.sleep(0.2)  # long enough for a second tap to arrive meanwhile
        self.calendars.append(f"cal{len(self.calendars) + 1}")
        return self.calendars[-1]

    def share_read_only(self, calendar_id, email):
        pass

    def mirror(self, calendar_id, shifts, tz):
        self.mirrored.append((calendar_id, len(shifts)))


def test_double_tap_on_google_button_makes_one_calendar(cfg, calendar_payload, tmp_path, monkeypatch):
    import threading

    from vaktkalender import web

    key = tmp_path / "sa.json"
    key.write_text("{}")
    cfg = dataclasses.replace(cfg, google_service_account_file=key)
    fake = FakeGoogle()
    monkeypatch.setattr(web, "GoogleCalendar", lambda _: fake)
    store = Store(cfg.db_path)
    snap = Snapshot(months=[(2026, 10), (2026, 11)])
    snap.add_calendar(calendar_payload, 558, "Bergen")
    store.apply_snapshot(snap, *scrape_window(date(2026, 10, 1), cfg.timezone)[1:])
    client = TestClient(create_app(cfg))
    client.post("/koble", data={"code": "onordmann"}, headers=ORIGIN)

    responses = []
    taps = [threading.Thread(target=lambda: responses.append(
        client.post("/google", headers=ORIGIN, follow_redirects=False))) for _ in range(2)]
    for t in taps:
        t.start()
    for t in taps:
        t.join()

    assert fake.calendars == ["cal1"]
    assert fake.mirrored == [("cal1", 3)]
    assert [r.headers["location"].endswith("cid=cal1") for r in responses] == [True, True]
    assert store.member("ola@example.com").last_sync_at


IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko)"
SAFARI = IPHONE + " Version/18.6 Mobile/15E148 Safari/604.1"
CHROME = IPHONE + " CriOS/140.0.7339.101 Mobile/15E148 Safari/604.1"
MESSENGER = IPHONE + " Mobile/22G86 [FBAN/MessengerForiOS;FBAV/530.0.0.0]"


def test_apple_button_warns_in_iphone_browsers_that_cannot_subscribe(client):
    client.post("/koble", data={"code": "onordmann"}, headers=ORIGIN)

    assert "Du har åpnet siden i" not in client.get("/", headers={"user-agent": SAFARI}).text
    assert "Du har åpnet siden i Chrome" in client.get("/", headers={"user-agent": CHROME}).text
    assert "Du har åpnet siden i Facebook/Messenger" in client.get("/", headers={"user-agent": MESSENGER}).text
    assert "Du har åpnet siden i en app" in client.get("/", headers={"user-agent": IPHONE}).text
    assert "Kopier kalenderlenken" in client.get("/", headers={"user-agent": SAFARI}).text


def test_admin_sees_when_a_calendar_app_last_fetched_the_feed(cfg, client):
    cfg = dataclasses.replace(cfg, dev_user_email="boss@example.com")
    home = client.post("/koble", data={"code": "onordmann"}, headers=ORIGIN)
    token = home.text.split("/ics/")[1].split(".ics")[0]

    assert client.head(f"/ics/{token}.ics").status_code == 200
    assert Store(cfg.db_path).member("ola@example.com").feed_fetched_at is None  # HEAD is only a check

    client.get(f"/ics/{token}.ics", headers={"user-agent": "iOS/18.6 (22G86) dataaccessd/1.0"})
    admin = TestClient(create_app(cfg)).get("/admin").text
    assert "iPhone/iPad" in admin
