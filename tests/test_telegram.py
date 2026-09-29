import dataclasses

import pytest
import requests

from vaktkalender import config, telegram

TOKEN = "123456:SECRET-token"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VAKT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("RBU_STORAGE_STATE", str(tmp_path / "missing.json"))
    return dataclasses.replace(config.load(), telegram_bot_token=TOKEN, telegram_chat_id="42")


def reply(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    return r


def test_sent(cfg, monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **kw: reply(200, '{"ok":true}'))
    assert telegram.notify(cfg, "hei") is True


def test_rejected_is_reported_without_leaking_token(cfg, monkeypatch, caplog):
    body = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    monkeypatch.setattr(requests, "post", lambda *a, **kw: reply(400, body))
    assert telegram.notify(cfg, "hei") is False
    assert "chat not found" in caplog.text
    assert TOKEN not in caplog.text


def test_network_error(cfg, monkeypatch):
    def fail(*a, **kw):
        raise requests.ConnectionError(f"https://api.telegram.org/bot{TOKEN}/sendMessage")

    monkeypatch.setattr(requests, "post", fail)
    assert telegram.notify(cfg, "hei") is False


def test_not_configured(cfg):
    assert telegram.notify(dataclasses.replace(cfg, telegram_chat_id=None), "hei") is False
