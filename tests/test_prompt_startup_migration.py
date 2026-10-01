"""Persistence and compatibility boundaries for stock prompt migration."""

from types import MappingProxyType, SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_PROMPT,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PROMPT,
    DOMAIN,
    LEGACY_DEFAULT_PROMPTS,
)


@pytest.mark.parametrize("version", [1, 2, CONFIG_ENTRY_VERSION])
@pytest.mark.parametrize(
    "stock_prompt", LEGACY_DEFAULT_PROMPTS, ids=["6.8.3", "previous"]
)
async def test_startup_migrates_only_exact_stock_conversation_prompts(
    version,
    stock_prompt,
) -> None:
    """Migration persists once, preserves custom/AI Task data, and is retry-safe."""
    original = {CONF_PROMPT: stock_prompt, "unrelated": "keep-me"}
    stock = SimpleNamespace(
        subentry_id="stock",
        subentry_type="conversation",
        data=MappingProxyType(original),
    )
    custom_prompt = stock_prompt + "\nCustom instruction"
    custom = SimpleNamespace(
        subentry_id="custom",
        subentry_type="conversation",
        data={CONF_PROMPT: custom_prompt},
    )
    ai_task = SimpleNamespace(
        subentry_id="task",
        subentry_type="ai_task_data",
        data={CONF_PROMPT: stock_prompt},
    )
    entry = SimpleNamespace(
        version=version,
        disabled_by=None,
        entry_id="entry",
        title="Agent",
        options={},
        subentries={item.subentry_id: item for item in (stock, custom, ai_task)},
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

    hass = SimpleNamespace(config_entries=ConfigEntries())
    await async_migrate_integration(hass)

    assert stock.data[CONF_PROMPT] == DEFAULT_PROMPT
    assert stock.data["unrelated"] == "keep-me"
    assert original[CONF_PROMPT] == stock_prompt
    assert custom.data[CONF_PROMPT] == custom_prompt
    assert ai_task.data == {CONF_PROMPT: stock_prompt}
    assert updates.count("stock") == 1
    assert "task" not in updates
    assert entry.version == CONFIG_ENTRY_VERSION
    first_updates = list(updates)

    await async_migrate_integration(hass)

    assert updates == first_updates
