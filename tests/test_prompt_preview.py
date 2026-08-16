"""Tests for effective-prompt ordering and side-effect-free preview rendering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    CONTINUE_CONVERSATION_CONDITIONAL,
    CONVERSATION_CONTINUITY_USER,
    MEMORY_MODE_AUTOMATIC,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _async_preview_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.prompt import (
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)
from homeassistant.exceptions import HomeAssistantError


def _options() -> dict:
    options = agent_config_defaults()
    options.update(
        {
            CONF_PROMPT: "USER-BEGIN\n{{ ha_name }} / {{ exposed_entities[0].state }}\nUSER-END",
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_ARCHIVE_ENABLED: True,
            CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_CONDITIONAL,
        }
    )
    return options


def _memory() -> MemoryRecord:
    return MemoryRecord(
        memory_id="persistent-1",
        user_id="admin",
        content="User prefers Celsius.",
        category="preference",
        source="explicit",
        created_at="2026-08-01T10:00:00+00:00",
        updated_at="2026-08-01T10:00:00+00:00",
    )


def _temporary() -> TemporaryMemoryRecord:
    return TemporaryMemoryRecord(
        memory_id="temporary-1",
        scope_id="user:admin",
        content="A delivery is due today.",
        category="delivery",
        source="automatic",
        expires_at="2099-08-16T23:59:00+01:00",
        created_at="2026-08-16T10:00:00+01:00",
        updated_at="2026-08-16T10:00:00+01:00",
    )


def test_effective_prompt_keeps_user_block_whole_and_moves_volatile_context_last(
    hass,
) -> None:
    """Stable integration guidance precedes request-varying context."""
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    result = render_effective_prompt(
        hass,
        _options(),
        exposed_entities=[{"state": "on"}],
        current_device_id=None,
        user_input=SimpleNamespace(text="not serialized"),
        skills=[],
        memories=[_memory()],
        temporary_memories=[_temporary()],
        knowledge_available=True,
    )

    expected_keys = [
        "user_prompt",
        "persistent_memory_instructions",
        "temporary_memory_instructions",
        "knowledge_instructions",
        "archive_instructions",
        "conditional_continuation_instructions",
        "persistent_memory_context",
        "temporary_memory_context",
    ]
    assert [section.key for section in result.sections] == expected_keys
    assert "USER-BEGIN\nCurrent Home / on\nUSER-END" in result.text
    assert result.text.index("USER-BEGIN") < result.text.index("USER-END")
    assert result.text.index("USER-END") < result.text.index("## Persistent memory")
    assert result.text.index("## Knowledge Library") < result.text.index(
        "Potentially relevant local memories"
    )
    assert result.text.index("Retained conversation archive") < result.text.index(
        "Current temporary context"
    )


async def test_preview_matches_production_builder_without_user_or_history(
    hass, monkeypatch
) -> None:
    """Preview and live execution share the same renderer and baseline assembly."""
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_KNOWLEDGE_ENABLED] = False
    options[CONF_ARCHIVE_ENABLED] = False
    options[CONF_TEMPORARY_MEMORY] = "off"
    subentry = SimpleNamespace(subentry_id="agent-1", data=options)
    entry = SimpleNamespace(entry_id="entry-1")
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: [{"state": "on"}],
    )

    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = subentry
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity._knowledge = None
    production = entity._build_system_prompt(
        [{"state": "on"}],
        SimpleNamespace(device_id=None),
        None,
    )
    preview = await _async_preview_effective_prompt(
        hass, entry, subentry, options, "admin"
    )

    assert preview["prompt"] == production
    assert "not serialized" not in preview["prompt"]
    assert any("conversation history are excluded" in note for note in preview["notes"])


async def test_preview_reads_temporary_context_without_mutation(
    hass, monkeypatch
) -> None:
    """Preview uses the read-only active snapshot and does not invoke mutation APIs."""
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_CONVERSATION_CONTINUITY] = CONVERSATION_CONTINUITY_USER
    options[CONF_KNOWLEDGE_ENABLED] = False
    manager = SimpleNamespace(
        async_active_snapshot=AsyncMock(return_value=[_temporary()]),
        async_active=AsyncMock(),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_loaded_temporary_memory",
        lambda *_args: manager,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: [{"state": "on"}],
    )

    result = await _async_preview_effective_prompt(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        SimpleNamespace(subentry_id="agent-1"),
        options,
        "admin",
    )

    manager.async_active_snapshot.assert_awaited_once_with("user:admin")
    manager.async_active.assert_not_awaited()
    assert "A delivery is due today" in result["prompt"]


async def test_preview_reads_stored_temporary_context_before_manager_load(
    hass, monkeypatch
) -> None:
    """An unsaved enablement draft uses a read-only storage snapshot."""
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_CONVERSATION_CONTINUITY] = CONVERSATION_CONTINUITY_USER
    options[CONF_KNOWLEDGE_ENABLED] = False
    read_snapshot = AsyncMock(return_value=[_temporary()])
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_loaded_temporary_memory",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_read_temporary_memory_snapshot",
        read_snapshot,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: [{"state": "on"}],
    )

    result = await _async_preview_effective_prompt(
        hass,
        SimpleNamespace(entry_id="entry-1"),
        SimpleNamespace(subentry_id="agent-1"),
        options,
        "admin",
    )

    read_snapshot.assert_awaited_once_with(hass, "entry-1", "agent-1", "user:admin")
    assert "A delivery is due today" in result["prompt"]


async def test_preview_render_failure_is_controlled(hass, monkeypatch) -> None:
    """Template failures become concise management errors without saving config."""
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: [],
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.render_effective_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad template")),
    )

    with pytest.raises(
        HomeAssistantError,
        match="The effective prompt could not be rendered: bad template",
    ):
        await _async_preview_effective_prompt(
            hass,
            SimpleNamespace(entry_id="entry-1"),
            SimpleNamespace(subentry_id="agent-1"),
            agent_config_defaults(),
            "admin",
        )
