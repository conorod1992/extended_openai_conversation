"""Residual lifecycle coverage for integration setup and migration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components import extended_openai_conversation_responses as integration


@pytest.mark.asyncio
async def test_unload_removes_request_rule_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtimes = {
        ("entry-1", "conversation-1"): object(),
        ("entry-1", "conversation-2"): object(),
        ("other-entry", "conversation-1"): object(),
    }
    unloaded_templates: list[str] = []

    class ConfigEntries:
        async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
            return True

    async def fake_unload_templates(_hass: object, entry_id: str) -> None:
        unloaded_templates.append(entry_id)

    monkeypatch.setattr(integration, "async_unload_templates", fake_unload_templates)
    hass = SimpleNamespace(
        data={integration._REQUEST_RULE_RUNTIMES: runtimes},
        config_entries=ConfigEntries(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        subentries={
            "one": SimpleNamespace(subentry_id="conversation-1"),
            "two": SimpleNamespace(subentry_id="conversation-2"),
        },
    )

    assert await integration.async_unload_entry(hass, entry) is True
    assert runtimes == {("other-entry", "conversation-1"): pytest.approx(runtimes[("other-entry", "conversation-1")])}
    assert unloaded_templates == ["entry-1"]


@pytest.mark.asyncio
async def test_current_entry_persists_stock_native_schema_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated: list[tuple[object, object, dict[str, Any]]] = []
    subentry = SimpleNamespace(
        subentry_id="conversation-1",
        subentry_type="conversation",
        data={integration.CONF_FUNCTION_TOOLS: "legacy"},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        version=integration.CONFIG_ENTRY_VERSION,
        disabled_by=None,
        subentries={"conversation-1": subentry},
    )

    class ConfigEntries:
        def async_entries(self, domain: str) -> list[object]:
            assert domain == integration.DOMAIN
            return [entry]

        def async_update_subentry(
            self, parent: object, child: object, *, data: dict[str, Any]
        ) -> None:
            updated.append((parent, child, data))

    monkeypatch.setattr(
        integration,
        "migrate_legacy_stock_native_function_tools_yaml",
        lambda configured: ("current", True),
    )
    hass = SimpleNamespace(config_entries=ConfigEntries())

    await integration.async_migrate_integration(hass)

    assert updated == [
        (
            entry,
            subentry,
            {integration.CONF_FUNCTION_TOOLS: "current"},
        )
    ]
