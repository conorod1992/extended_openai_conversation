"""Regression coverage for release-created gpt-5-mini/Flex configuration."""

from copy import deepcopy
from types import MappingProxyType, SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    _migrate_saved_service_tier,
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    agent_config_snapshot,
    merge_agent_config,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


@pytest.mark.parametrize("version", [1, 2, CONFIG_ENTRY_VERSION])
async def test_legacy_default_is_persisted_readable_editable_and_requestable(
    version, hass
):
    original = {**agent_config_defaults(), "service_tier": "flex", "unrelated": "keep"}
    stock = SimpleNamespace(
        subentry_id="stock",
        subentry_type="conversation",
        data=MappingProxyType(deepcopy(original)),
    )
    task = SimpleNamespace(
        subentry_id="task", subentry_type="ai_task_data", data=deepcopy(original)
    )
    entry = SimpleNamespace(
        version=version,
        disabled_by=None,
        entry_id="entry",
        title="Agent",
        options={},
        subentries={"stock": stock, "task": task},
    )
    updates = []

    class ConfigEntries:
        def async_entries(self, domain):
            assert domain == DOMAIN
            return [entry]

        def async_update_subentry(self, parent, child, *, data):
            assert parent is entry
            updates.append(child.subentry_id)
            child.data = MappingProxyType(data)

        def async_update_entry(self, parent, **changes):
            for key, value in changes.items():
                setattr(parent, key, value)

    migration_hass = SimpleNamespace(config_entries=ConfigEntries())
    await async_migrate_integration(migration_hass)

    assert stock.data["service_tier"] == "default"
    assert stock.data["chat_model"] == "gpt-5-mini"
    assert stock.data["unrelated"] == "keep"
    assert original["service_tier"] == "flex"
    assert task.data == original
    assert updates == ["stock"]
    snapshot = agent_config_snapshot(dict(stock.data))
    edited = merge_agent_config(dict(stock.data), {"prompt": "Edited after upgrade"})
    assert snapshot["service_tier"] == edited["service_tier"] == "default"
    request = build_provider_request_snapshot(edited, {"api_provider": "openai"})
    assert request.api_kwargs["service_tier"] == "default"

    # A cold start with the persisted mapping requires no second repair.
    await async_migrate_integration(migration_hass)
    assert updates == ["stock"]
    with pytest.raises(AgentConfigError, match="service_tier"):
        merge_agent_config(dict(stock.data), {"service_tier": "flex"})
    with pytest.raises(AgentConfigError, match="service_tier"):
        normalize_agent_config({"chat_model": "gpt-5-mini", "service_tier": "flex"})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"chat_model": "gpt-5-mini"},
        {"chat_model": "gpt-5-mini", "service_tier": "default"},
        {"chat_model": "gpt-5-mini", "service_tier": "auto"},
        {"chat_model": "gpt-5-mini", "service_tier": "priority"},
        {"chat_model": "gpt-5-mini", "service_tier": "made-up"},
        {"chat_model": "gpt-5.6-terra", "service_tier": "flex"},
        {"chat_model": "gpt-4.1", "service_tier": "flex"},
        {
            "chat_model": "gpt-5-mini",
            "azure_model": "gpt-5.6-terra",
            "service_tier": "flex",
        },
    ],
)
def test_other_saved_settings_are_preserved(data):
    before = deepcopy(data)
    assert not _migrate_saved_service_tier(data)
    assert data == before


def test_omitted_model_uses_historical_default():
    data = {"service_tier": "flex"}
    assert _migrate_saved_service_tier(data)
    assert data == {"service_tier": "default"}


def test_catalog_supported_flex_is_preserved(monkeypatch):
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.get_model_config",
        lambda model: {"service_tier_options": ["auto", "default", "flex"]},
    )
    data = {"chat_model": "gpt-5-mini", "service_tier": "flex"}
    assert not _migrate_saved_service_tier(data)
    assert data["service_tier"] == "flex"
