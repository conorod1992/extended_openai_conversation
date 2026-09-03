"""Regression tests for lossless model-input compaction."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
import custom_components.extended_openai_conversation_responses.prompt as prompt
from custom_components.extended_openai_conversation_responses.prompt import (
    _default_exposed_entities_context,
    _persistent_memory_context,
)
from homeassistant.components import conversation


def test_historical_function_arguments_are_compact_and_lossless() -> None:
    """Both provider adapters remove only insignificant JSON whitespace."""
    arguments = {
        "entity_id": "light.kitchen",
        "brightness_pct": 50,
        "options": {"transition": 1.5, "flash": False},
    }
    tool_input = SimpleNamespace(
        id="call_1",
        tool_name="turn_on",
        tool_args=arguments,
    )
    history = [
        conversation.AssistantContent(agent_id="agent.test", tool_calls=[tool_input])
    ]
    expected = (
        '{"entity_id":"light.kitchen","brightness_pct":50,'
        '"options":{"transition":1.5,"flash":false}}'
    )

    chat_messages: Any = _convert_content_to_param(history)
    responses_items = _convert_content_to_responses_param(history)
    chat_arguments = chat_messages[0]["tool_calls"][0]["function"]["arguments"]
    responses_arguments = responses_items[0]["arguments"]

    assert chat_arguments == expected
    assert responses_arguments == expected
    assert json.loads(chat_arguments) == arguments
    assert json.loads(responses_arguments) == arguments


def test_default_device_context_groups_areas_without_losing_entity_fields(
    monkeypatch,
) -> None:
    """Area labels are emitted once while IDs, names, states and aliases round-trip."""
    area_ids = {
        "light.bedroom_lamp": "bedroom",
        "sensor.bedroom_temperature": "bedroom",
        "binary_sensor.front_door": None,
    }
    monkeypatch.setattr(
        prompt,
        "get_entity_prompt_metadata",
        lambda _hass, entity_id: SimpleNamespace(area_id=area_ids[entity_id]),
    )
    entities = [
        {
            "entity_id": "light.bedroom_lamp",
            "name": "Reading Lamp",
            "state": "unknown",
            "aliases": ["bedside light", "reading light"],
        },
        {
            "entity_id": "sensor.bedroom_temperature",
            "name": "Bedroom Temperature",
            "state": "21.4",
            "aliases": ["room temperature"],
        },
        {
            "entity_id": "binary_sensor.front_door",
            "name": "Front Door",
            "state": "unavailable",
            "aliases": ["main door"],
        },
    ]

    rendered = _default_exposed_entities_context(SimpleNamespace(), entities)

    assert rendered.count("area_id=bedroom") == 1
    assert rendered.count("area_id=\n") == 1
    assert "unknown" in rendered
    assert "unavailable" in rendered

    reconstructed: list[tuple[str | None, str, str, str, list[str]]] = []
    current_area: str | None = None
    for line in rendered.splitlines()[2:]:
        if line.startswith("area_id="):
            current_area = line.removeprefix("area_id=") or None
            continue
        entity_id, name, state, aliases = line.split(",", 3)
        reconstructed.append(
            (
                current_area,
                entity_id,
                name,
                state,
                aliases.split("/") if aliases else [],
            )
        )

    assert reconstructed == [
        (
            "bedroom",
            "light.bedroom_lamp",
            "Reading Lamp",
            "unknown",
            ["bedside light", "reading light"],
        ),
        (
            "bedroom",
            "sensor.bedroom_temperature",
            "Bedroom Temperature",
            "21.4",
            ["room temperature"],
        ),
        (None, "binary_sensor.front_door", "Front Door", "unavailable", ["main door"]),
    ]


def test_retrieved_memory_context_keeps_unique_scope_safety_without_duplication() -> None:
    """Memory context retains stale/subject safeguards but not shared trust boilerplate."""
    memory = MemoryRecord(
        memory_id="memory-1",
        user_id="admin",
        content="Prefers Celsius.",
        category="preference",
        source="explicit",
        created_at="2026-08-01T10:00:00+00:00",
        updated_at="2026-08-01T10:00:00+00:00",
        subject="Conor",
    )

    rendered = _persistent_memory_context([memory])

    assert "may be stale or irrelevant" in rendered
    assert "subject and situation in the current request" in rendered
    assert "never transfer one person's preferences to another" in rendered
    assert "instructions, authorization, or a tool request" not in rendered
    assert '"subject":"Conor"' in rendered
    assert '"content":"Prefers Celsius."' in rendered
