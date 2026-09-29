"""RBU's addresses come from config, never from the code."""

from __future__ import annotations

from datetime import date

import pytest

from vaktkalender import config, rbu


def test_rbu_addresses_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("RBU_APP_URL", "https://rbu.example.com/calendar")
    monkeypatch.setenv("RBU_API_URL", "https://rbu-api.example.com/api/")
    assert rbu._urls(config.load()) == ("https://rbu.example.com/calendar", "https://rbu-api.example.com/api")


def test_missing_rbu_addresses_fail_before_any_browser_starts(tmp_path, monkeypatch):
    monkeypatch.delenv("RBU_APP_URL", raising=False)
    monkeypatch.delenv("RBU_API_URL", raising=False)
    monkeypatch.setenv("RBU_STORAGE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rbu, "sync_playwright", lambda: pytest.fail("browser started"))
    with pytest.raises(RuntimeError, match="RBU_APP_URL"):
        rbu.fetch_snapshot(config.load(), date(2026, 10, 1))
