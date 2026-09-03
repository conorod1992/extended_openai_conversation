"""Tests for request-local schemas and static entity metadata caching."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.extended_openai_conversation_responses.entity_context_cache import (
    EntityPromptMetadata,
    _CACHE_KEY,
    get_entity_prompt_metadata,
)
from custom_components.extended_openai_conversation_responses.request_static_cache import (
    _FORMATTED_TOOLS,
    cached_format_tools,
    render_maintained_entity_context,
    tools_for_available_skills,
)


def _skill_loader(*, canonical: bool = True) -> dict:
    return {
        "spec": {"name": "load_skill", "description": "Load a skill file"},
        "function": {
            "type": "read_file",
            "path": (
                "{{extended_openai.skill_dir(name)}}/{{file}}"
                if canonical
                else "/config/custom/{{file}}"
            ),
        },
    }


def test_provider_formatting_is_reused_only_inside_one_request() -> None:
    """Identical tool objects/mode are formatted once in a live request context."""
    calls = 0
    tools = [{"spec": {"name": "one"}}]

    def formatter(value, api_mode):
        nonlocal calls
        calls += 1
        return [{"mode": api_mode, "name": value[0]["spec"]["name"]}]

    token = _FORMATTED_TOOLS.set({})
    try:
        first = cached_format_tools(tools, "responses", formatter)
        second = cached_format_tools(tools, "responses", formatter)
    finally:
        _FORMATTED_TOOLS.reset(token)

    assert calls == 1
    assert first == second
    assert first is not second

    cached_format_tools(tools, "responses", formatter)
    cached_format_tools(tools, "responses", formatter)
    assert calls == 3


def test_provider_format_cache_key_tracks_mode_and_effective_tool_objects() -> None:
    calls = 0

    def formatter(value, api_mode):
        nonlocal calls
        calls += 1
        return [{"mode": api_mode, "count": len(value)}]

    first_tools = [{"spec": {"name": "one"}}]
    changed_tools = [*first_tools, {"spec": {"name": "two"}}]
    token = _FORMATTED_TOOLS.set({})
    try:
        cached_format_tools(first_tools, "responses", formatter)
        cached_format_tools(first_tools, "chat_completions", formatter)
        cached_format_tools(changed_tools, "responses", formatter)
    finally:
        _FORMATTED_TOOLS.reset(token)

    assert calls == 3


def test_zero_skills_remove_only_canonical_loader() -> None:
    """A same-name custom tool is never hidden by a name-only heuristic."""
    canonical = _skill_loader(canonical=True)
    custom = _skill_loader(canonical=False)
    other = {"spec": {"name": "other"}, "function": {"type": "template"}}

    assert tools_for_available_skills([canonical, other], False) == [other]
    assert tools_for_available_skills([canonical, other], True) == [canonical, other]
    assert tools_for_available_skills([custom, other], False) == [custom, other]


def test_maintained_entity_renderer_preserves_live_rows_and_cached_area() -> None:
    """The direct renderer uses current row values and the PR #98 sparse name field."""
    entities = [
        {
            "entity_id": "light.study_light",
            "name": "Study Light",
            "prompt_name": "",
            "state": "on",
            "aliases": ["desk light"],
        },
        {
            "entity_id": "sensor.phone_alarm",
            "name": "Conor's Phone Next Alarm",
            "prompt_name": "Conor's Phone Next Alarm",
            "state": "07:30",
            "aliases": [],
        },
    ]
    with patch(
        "custom_components.extended_openai_conversation_responses.request_static_cache.get_entity_prompt_metadata",
        side_effect=[
            EntityPromptMetadata(("desk light",), "study"),
            EntityPromptMetadata((), None),
        ],
    ):
        rendered = render_maintained_entity_context(object(), entities)

    assert rendered == (
        "## Available Devices\n"
        "entity_id,name,state,area_id,aliases\n"
        "light.study_light,,on,study,desk light\n"
        "sensor.phone_alarm,Conor's Phone Next Alarm,07:30,,\n"
    )
    assert "```" not in rendered


def test_entity_metadata_cache_reuses_values_and_registry_events_clear_it() -> None:
    """Static metadata is reused until an entity/device registry update fires."""
    listeners = {}
    hass = SimpleNamespace(
        data={},
        bus=SimpleNamespace(
            async_listen=lambda event_type, callback: listeners.__setitem__(
                event_type, callback
            )
        ),
    )
    first = EntityPromptMetadata(("desk",), "study")
    second = EntityPromptMetadata(("new desk",), "office")

    with patch(
        "custom_components.extended_openai_conversation_responses.entity_context_cache._build_metadata",
        Mock(side_effect=[first, second]),
    ) as builder:
        assert get_entity_prompt_metadata(hass, "light.study") is first
        assert get_entity_prompt_metadata(hass, "light.study") is first
        assert builder.call_count == 1
        assert hass.data[_CACHE_KEY]

        callback = next(iter(listeners.values()))
        callback(SimpleNamespace())
        assert hass.data[_CACHE_KEY] == {}
        assert get_entity_prompt_metadata(hass, "light.study") is second
        assert builder.call_count == 2
