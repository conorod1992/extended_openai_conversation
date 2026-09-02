"""Regression tests for lossless model-facing dynamic context compaction."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CURRENT_DATETIME_TEMPLATE,
    CONF_EXPOSED_ENTITIES_TEMPLATE,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    MEMORY_MODE_AUTOMATIC,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.prompt import (
    _default_prompt_entities,
    _entity_prompt_name,
    _persistent_memory_context,
    _temporary_memory_context,
    render_effective_prompt,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)


def _setup_area_template_registries(hass) -> None:
    hass.data[ar.DATA_REGISTRY] = SimpleNamespace(
        async_get_area_by_name=lambda _value: None,
        async_get_areas_by_alias=lambda _value: [],
    )
    hass.data[dr.DATA_REGISTRY] = SimpleNamespace(async_get=lambda _value: None)
    hass.data[er.DATA_REGISTRY] = SimpleNamespace(async_get=lambda _value: None)


def test_entity_name_is_omitted_only_for_exact_slug_duplicate() -> None:
    assert (
        _entity_prompt_name(
            {"entity_id": "light.study_light", "name": "Study Light"}
        )
        == ""
    )
    assert (
        _entity_prompt_name(
            {"entity_id": "light.lamp_plug_switch_1", "name": "Bedroom Lamp"}
        )
        == "Bedroom Lamp"
    )
    assert (
        _entity_prompt_name(
            {"entity_id": "light.study_light_2", "name": "Study Light"}
        )
        == "Study Light"
    )


def test_prompt_entity_projection_preserves_original_data_and_all_aliases() -> None:
    entity = {
        "entity_id": "light.study_light",
        "name": "Study Light",
        "state": "on",
        "aliases": ["desk light", "office light"],
    }
    projected = _default_prompt_entities([entity])

    assert projected == [
        {
            **entity,
            "prompt_name": "",
        }
    ]
    assert projected[0]["aliases"] == ["desk light", "office light"]
    assert entity["name"] == "Study Light"
    assert "prompt_name" not in entity


def test_default_entity_context_is_compact_but_keeps_every_entity_and_alias(hass) -> None:
    _setup_area_template_registries(hass)
    options = agent_config_defaults()
    options[CONF_PROMPT] = "BASE"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = "fixed"
    options[CONF_EXPOSED_ENTITIES_TEMPLATE] = ""
    entities = [
        {
            "entity_id": "light.study_light",
            "name": "Study Light",
            "state": "on",
            "aliases": ["desk light", "office light"],
        },
        {
            "entity_id": "light.lamp_plug_switch_1",
            "name": "Bedroom Lamp",
            "state": "off",
            "aliases": [],
        },
    ]

    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=entities,
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    context = next(
        section.text for section in result.sections if section.key == "exposed_entities_context"
    )

    assert "```" not in context
    assert "entity_id,name_if_different,state,area_id,aliases" in context
    assert "light.study_light,,on,,desk light/office light" in context
    assert "light.lamp_plug_switch_1,Bedroom Lamp,off,," in context
    assert context.count("light.study_light") == 1
    assert context.count("light.lamp_plug_switch_1") == 1


def test_custom_entity_template_keeps_original_friendly_name(hass) -> None:
    options = agent_config_defaults()
    options[CONF_PROMPT] = "BASE"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = "fixed"
    options[CONF_EXPOSED_ENTITIES_TEMPLATE] = "{{ exposed_entities[0].name }}"

    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=[
            {
                "entity_id": "light.study_light",
                "name": "Study Light",
                "state": "on",
                "aliases": [],
            }
        ],
        current_device_id=None,
        user_input=None,
        skills=[],
    )

    context = next(
        section.text for section in result.sections if section.key == "exposed_entities_context"
    )
    assert context == "Study Light"


def test_default_datetime_keeps_seconds_and_timezone_without_microseconds(hass) -> None:
    options = agent_config_defaults()
    options[CONF_PROMPT] = "BASE"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = ""
    options[CONF_EXPOSED_ENTITIES_TEMPLATE] = "custom"

    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=None,
        skills=[],
    )
    context = next(
        section.text for section in result.sections if section.key == "current_datetime_context"
    )
    value = context.splitlines()[-1]

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", value)
    assert "." not in value


def test_persistent_memory_context_preserves_record_fields_with_compact_json() -> None:
    memory = MemoryRecord(
        memory_id="memory-1",
        user_id="shared:household",
        content="The boiler is serviced each October.",
        category="household",
        source="explicit",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-02-01T00:00:00+00:00",
        importance="high",
        subject="boiler",
        key="boiler-service",
        valid_from="2026-02-01T00:00:00+00:00",
        last_confirmed_at="2026-08-01T00:00:00+00:00",
    )

    context = _persistent_memory_context([memory])
    serialized = context.split("\n", 1)[1]
    assert json.loads(serialized) == [
        {
            "memory_id": "memory-1",
            "scope": "shared_household",
            "category": "household",
            "importance": "high",
            "subject": "boiler",
            "key": "boiler-service",
            "valid_from": "2026-02-01T00:00:00+00:00",
            "last_confirmed_at": "2026-08-01T00:00:00+00:00",
            "content": "The boiler is serviced each October.",
        }
    ]
    assert ", " not in serialized
    assert ": " not in serialized
    assert "never transfer one person's preferences to another" in context


def _temporary(memory_id: str, category: str) -> TemporaryMemoryRecord:
    return TemporaryMemoryRecord(
        memory_id=memory_id,
        scope_id="conversation:one",
        content=f"Temporary fact {memory_id}",
        category=category,
        source="automatic",
        expires_at="2026-09-03T23:59:00+01:00",
        created_at="2026-09-03T10:00:00+01:00",
        updated_at="2026-09-03T10:00:00+01:00",
    )


def test_temporary_memory_omits_only_default_category_and_explains_default() -> None:
    context = _temporary_memory_context(
        [_temporary("general-1", "general"), _temporary("delivery-1", "delivery")]
    )
    serialized = context.split("\n", 1)[1]
    records = json.loads(serialized)

    assert records[0] == {
        "memory_id": "general-1",
        "content": "Temporary fact general-1",
        "expires_at": "2026-09-03T23:59:00+01:00",
    }
    assert records[1]["category"] == "delivery"
    assert "omitted category means general" in context
    assert ", " not in serialized
    assert ": " not in serialized


def test_full_prompt_uses_compact_memory_context_without_disabling_features(hass) -> None:
    options = agent_config_defaults()
    options[CONF_PROMPT] = "BASE"
    options[CONF_CURRENT_DATETIME_TEMPLATE] = "fixed"
    options[CONF_EXPOSED_ENTITIES_TEMPLATE] = "custom"
    options[CONF_MEMORY_MODE] = MEMORY_MODE_AUTOMATIC
    options[CONF_TEMPORARY_MEMORY] = TEMPORARY_MEMORY_BALANCED

    result = render_effective_prompt(
        hass,
        options,
        exposed_entities=[],
        current_device_id=None,
        user_input=None,
        skills=[],
        memories=[
            MemoryRecord(
                memory_id="memory-1",
                user_id="admin",
                content="User prefers Celsius.",
                category="preference",
                source="explicit",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ],
        temporary_memories=[_temporary("temporary-1", "general")],
    )

    keys = {section.key for section in result.sections}
    assert "persistent_memory_instructions" in keys
    assert "persistent_memory_context" in keys
    assert "temporary_memory_instructions" in keys
    assert "temporary_memory_context" in keys
