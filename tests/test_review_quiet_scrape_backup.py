"""Focused guards for quiet-control identity, scrape schemas and backup executor IO."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.exceptions import (
    InvalidFunction,
)

from custom_components.extended_openai_conversation_responses import quiet_hours as qh
from custom_components.extended_openai_conversation_responses.functions.web import (
    ScrapeFunction,
)


def test_scrape_requires_nonempty_sensor_before_any_network_request():
    function = ScrapeFunction()
    # Missing extraction is not a valid executable Scrape configuration.
    with pytest.raises(InvalidFunction):
        function.validate_schema({"type": "scrape", "resource": "https://example.com"})
    with pytest.raises(InvalidFunction):
        function.validate_schema(
            {"type": "scrape", "resource": "https://example.com", "sensor": []}
        )


def test_missing_registry_identity_releases_stale_quiet_hours_ownership(monkeypatch):
    class FakeRegistry:
        def async_get_entity_id(self, *identity):
            return None

    monkeypatch.setattr(qh.er, "async_get", lambda hass: FakeRegistry())
    owner = SimpleNamespace(
        hass=object(),
        _active={
            "controls": {
                "switch.reused": {
                    "registry_identity": ["switch", "example", "old-device"],
                    "original_value": True,
                    "quiet_value": False,
                }
            },
            "pending_controls": {},
            "observed_controls": ["switch.reused"],
        },
    )
    qh.QuietHoursManager._migrate_control_entity_ids(owner)
    assert owner._active["controls"] == {}
    assert owner._active["observed_controls"] == []


async def test_legacy_backup_finalization_uses_executor(monkeypatch):
    from custom_components.extended_openai_conversation_responses import backup

    captured = []
    snapshot = {
        "agent": {"title": "Test", "config": {}},
        "request_rules": {},
        "created_at": "2026-10-13",
    }

    class Gate:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        backup,
        "get_agent_maintenance_gate",
        lambda *_args: SimpleNamespace(exclusive=lambda: Gate()),
    )
    monkeypatch.setattr(
        backup,
        "async_collect_backup_snapshot",
        AsyncMock(return_value=snapshot),
    )

    async def fake_to_thread(func, *args):
        captured.append((func, args))
        return {"json": "done"}

    monkeypatch.setattr(backup.asyncio, "to_thread", fake_to_thread)
    hass = SimpleNamespace()
    result = await backup.async_create_backup(
        hass, SimpleNamespace(entry_id="entry"), SimpleNamespace(subentry_id="agent")
    )
    assert result == {"json": "done"}
    assert captured == [(backup.finalize_backup_snapshot, (snapshot,))]
