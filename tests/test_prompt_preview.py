"""Tests for effective-prompt ordering and side-effect-free preview rendering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_CURRENT_DATETIME_TEMPLATE,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_EXPOSED_ENTITIES_TEMPLATE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    CONF_WEB_SEARCH,
    CONTINUE_CONVERSATION_CONDITIONAL,
    CONVERSATION_CONTINUITY_USER,
    DEFAULT_PROMPT,
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
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)


def test_new_agent_defaults_use_first_class_volatile_context() -> None:
    """New agents opt in without duplicating volatile context in the user prompt."""
    options = agent_config_defaults()

    assert options[CONF_CURRENT_DATETIME_ENABLED] is True
    assert options[CONF_EXPOSED_ENTITIES_ENABLED] is True
    assert options[CONF_CURRENT_DATETIME_TEMPLATE] == ""
    assert options[CONF_EXPOSED_ENTITIES_TEMPLATE] == ""
    assert options[CONF_PROMPT] == DEFAULT_PROMPT
    assert "now()" not in DEFAULT_PROMPT
    assert "exposed_entities" not in DEFAULT_PROMPT


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


def _setup_area_template_registries(hass) -> None:
    """Set up registries required by Home Assistant's area_id template helper."""
    hass.data[ar.DATA_REGISTRY] = ar.AreaRegistry(hass)
    hass.data[dr.DATA_REGISTRY] = dr.DeviceRegistry(hass)
    hass.data[er.DATA_REGISTRY] = er.EntityRegistry(hass)


def test_effective_prompt_keeps_user_block_whole_and_moves_volatile_context_last(
    hass,
) -> None:
    """Stable integration guidance precedes request-varying context."""
    _setup_area_template_registries(hass)
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    result = render_effective_prompt(
        hass,
        _options(),
        exposed_entities=[
            {
                "entity_id": "light.bedroom",
                "name": "Bedroom Lamp",
                "state": "on",
                "aliases": [],
            }
        ],
        current_device_id=None,
        user_input=SimpleNamespace(text="not serialized"),
        skills=[],
        memories=[_memory()],
        temporary_memories=[_temporary()],
        knowledge_available=True,
    )

    expected_keys = [
        "persistent_memory_instructions",
        "temporary_memory_instructions",
        "knowledge_instructions",
        "archive_instructions",
        "conditional_continuation_instructions",
        "user_prompt",
        "current_datetime_context",
        "exposed_entities_context",
        "persistent_memory_context",
        "temporary_memory_context",
    ]
    assert [section.key for section in result.sections] == expected_keys
    assert "USER-BEGIN\nCurrent Home / on\nUSER-END" in result.text
    assert result.text.index("USER-BEGIN") < result.text.index("USER-END")
    assert result.text.index("## Persistent memory") < result.text.index("USER-BEGIN")
    assert result.text.index("USER-END") < result.text.index("## Current date and time")
    assert result.text.index("## Current date and time") < result.text.index(
        "## Available Devices"
    )
    assert "entity_id,name,state,area_id,aliases" in result.text
    assert result.text.index("## Knowledge Library") < result.text.index(
        "Potentially relevant local memories"
    )
    assert result.text.index("Retained conversation archive") < result.text.index(
        "Current temporary context"
    )


def _entity_context() -> list[dict]:
    return [
        {
            "entity_id": "light.bedroom",
            "name": "Bedroom Lamp",
            "state": "on",
            "aliases": [],
        }
    ]


def test_generated_context_toggles_and_custom_templates(hass) -> None:
    """Date/device blocks are independent and custom formatting stays late."""
    options = agent_config_defaults()
    options[CONF_PROMPT] = "USER BLOCK"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = "## Clock\nLOCAL TIME"
    options[CONF_EXPOSED_ENTITIES_TEMPLATE] = (
        "## Devices\n{{ exposed_entities[0].name }}={{ exposed_entities[0].state }}"
    )
    entities = [{"name": "Lamp", "state": "on"}]
    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=entities,
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    assert "USER BLOCK\n## Clock\nLOCAL TIME\n## Devices\nLamp=on" in result.text

    options[CONF_CURRENT_DATETIME_ENABLED] = False
    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=entities,
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    assert "LOCAL TIME" not in result.text
    assert "Lamp=on" in result.text

    options[CONF_EXPOSED_ENTITIES_ENABLED] = False
    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=entities,
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    assert "Lamp=on" not in result.text


def test_invalid_custom_context_template_is_rejected_cleanly() -> None:
    """Custom overrides are syntax-validated before save."""
    with pytest.raises(AgentConfigError, match="invalid template"):
        normalize_agent_config({CONF_CURRENT_DATETIME_TEMPLATE: "{% if %}"})


async def test_preview_matches_production_builder_without_user_or_history(
    hass, monkeypatch
) -> None:
    _setup_area_template_registries(hass)
    """Preview and live execution share the same renderer and baseline assembly."""
    hass.config.location_name = "Current Home"
    hass.config.time_zone = "Europe/Dublin"
    options = _options()
    options[CONF_KNOWLEDGE_ENABLED] = False
    options[CONF_ARCHIVE_ENABLED] = False
    options[CONF_TEMPORARY_MEMORY] = "off"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = "## Current date and time\nfixed"
    subentry = SimpleNamespace(subentry_id="agent-1", data=options)
    entry = SimpleNamespace(entry_id="entry-1")
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: _entity_context(),
    )

    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = subentry
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity._knowledge = None
    production = entity._build_system_prompt(
        _entity_context(),
        SimpleNamespace(device_id=None),
        None,
    )
    preview = await _async_preview_effective_prompt(
        hass, entry, subentry, options, "admin"
    )

    assert preview["prompt"] == production
    assert "not serialized" not in preview["prompt"]
    assert any("conversation history are excluded" in note for note in preview["notes"])
    assert preview["function_group_savings"]["characters"] == 0


async def test_preview_reads_temporary_context_without_mutation(
    hass, monkeypatch
) -> None:
    _setup_area_template_registries(hass)
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
        lambda _hass: _entity_context(),
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
    _setup_area_template_registries(hass)
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
        lambda _hass: _entity_context(),
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
        match="The effective request could not be assembled: bad template",
    ):
        await _async_preview_effective_prompt(
            hass,
            SimpleNamespace(entry_id="entry-1"),
            SimpleNamespace(subentry_id="agent-1"),
            agent_config_defaults(),
            "admin",
        )


async def test_effective_request_preview_uses_first_request_tool_assembly(
    hass, monkeypatch
) -> None:
    """Preview withholds on-demand schemas and reports exact local counts."""
    options = agent_config_defaults()
    options.update(
        {
            CONF_PROMPT: "BASE",
            CONF_API_MODE: "responses",
            CONF_CHAT_MODEL: "gpt-5.6-mini",
            CONF_WEB_SEARCH: True,
            CONF_ARCHIVE_ENABLED: False,
            CONF_CURRENT_DATETIME_ENABLED: False,
            CONF_EXPOSED_ENTITIES_ENABLED: False,
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                [
                    {
                        "spec": {
                            "name": "eager_tool",
                            "description": "Eager schema",
                            "parameters": {"type": "object", "properties": {}},
                        },
                        "function": {"type": "native", "name": "execute_service"},
                    },
                    {
                        "spec": {
                            "name": "demand_tool",
                            "description": "Large on-demand schema",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "long_argument": {
                                        "type": "string",
                                        "description": "x" * 500,
                                    }
                                },
                            },
                        },
                        "function": {"type": "native", "name": "execute_service"},
                    },
                    {
                        "enabled": False,
                        "spec": {
                            "name": "disabled_tool",
                            "description": "Disabled",
                            "parameters": {"type": "object", "properties": {}},
                        },
                        "function": {"type": "native", "name": "execute_service"},
                    },
                ],
                sort_keys=False,
            ),
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "demand",
                    "name": "Demand tools",
                    "description": "Load only when needed",
                    "loading_mode": "on_demand",
                    "functions": ["demand_tool", "disabled_tool"],
                }
            ],
        }
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.get_exposed_entities",
        lambda _hass: [],
    )
    result = await _async_preview_effective_prompt(
        hass,
        SimpleNamespace(entry_id="entry-1", data={}),
        SimpleNamespace(subentry_id="agent-1"),
        options,
        "admin",
    )

    sections = {section["key"]: section for section in result["sections"]}
    assert "eager_tool" in sections["function_tools"]["content"]
    assert "demand_tool" not in sections["function_tools"]["content"]
    assert "disabled_tool" not in "".join(
        section["content"] for section in result["sections"]
    )
    assert "load_function_groups" in sections["function_group_loader"]["content"]
    assert "web_search" in sections["provider_tools"]["content"]
    assert '"api_mode":"responses"' in sections["request_settings"]["content"]
    assert result["total_character_count"] == sum(
        section["character_count"] for section in result["sections"]
    )
    assert result["function_group_savings"]["characters"] > 0
    assert (
        result["function_group_savings"]["grouped_characters"]
        < result["function_group_savings"]["without_on_demand_grouping_characters"]
    )
