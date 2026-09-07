"""Regression tests for stable exposed-entity attribute context."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import exposed_attributes
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_EXPOSED_ENTITIES_ENABLED,
    CONF_PROMPT,
)
from custom_components.extended_openai_conversation_responses.exposed_attributes import (
    CONF_EXPOSED_ENTITY_ATTRIBUTES,
    MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS,
    _attribute_item_size,
    _render_grouped_default_with_attributes,
    _render_legacy_default_with_attributes,
    _safe_attribute_value,
    _validate_preferences,
    _wrap_effective_prompt_renderer,
    enrich_exposed_entities,
    exposed_attribute_catalog,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _async_preview_effective_prompt,
    _export_agent,
    _parse_import_document,
)
from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses import prompt
from homeassistant.helpers import entity_registry as er


def _entry(entry_id: str, entity_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=entry_id, entity_id=entity_id)


def _registry(*entries: SimpleNamespace) -> SimpleNamespace:
    by_id = {entry.id: entry for entry in entries}
    by_entity = {entry.entity_id: entry for entry in entries}
    return SimpleNamespace(
        async_get=lambda entity_id: by_entity.get(entity_id),
        entities=SimpleNamespace(
            get_entry=lambda entry_id: by_id.get(entry_id),
            values=lambda: by_id.values(),
        ),
    )


def _install_registry(hass, *entries: SimpleNamespace) -> SimpleNamespace:
    registry = _registry(*entries)
    hass.data[er.DATA_REGISTRY] = registry
    return registry


def _state(attributes: dict) -> SimpleNamespace:
    return SimpleNamespace(attributes=attributes)


def _entity(entity_id: str, name: str = "Lamp") -> dict:
    return {"entity_id": entity_id, "name": name, "state": "on", "aliases": []}


def test_agent_config_stores_only_stable_references_and_attribute_names() -> None:
    normalized = normalize_agent_config(
        {
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:stable-b": ["zeta", "alpha", "zeta"],
                "registry:stable-a": [],
            }
        }
    )

    assert normalized[CONF_EXPOSED_ENTITY_ATTRIBUTES] == {
        "registry:stable-b": ["alpha", "zeta"]
    }
    assert all(
        isinstance(attributes, list)
        for attributes in normalized[CONF_EXPOSED_ENTITY_ATTRIBUTES].values()
    )


@pytest.mark.parametrize(
    "value",
    [
        {"light.kitchen": ["brightness"]},
        {"registry: stable": ["brightness"]},
        {"registry:stable": "brightness"},
        {"registry:stable": [" brightness"]},
    ],
)
def test_invalid_or_mutable_preferences_are_rejected(value) -> None:
    with pytest.raises(AgentConfigError):
        _validate_preferences(value)


def test_import_export_round_trip_preserves_preferences_without_live_values() -> None:
    data = agent_config_defaults()
    data[CONF_EXPOSED_ENTITY_ATTRIBUTES] = {
        "registry:stable": ["brightness", "color_temp"]
    }
    document = _export_agent(SimpleNamespace(title="Jarvis", data=data))
    parsed = _parse_import_document(document)

    assert parsed["config"][CONF_EXPOSED_ENTITY_ATTRIBUTES] == {
        "registry:stable": ["brightness", "color_temp"]
    }
    assert "255" not in json.dumps(
        parsed["config"][CONF_EXPOSED_ENTITY_ATTRIBUTES], sort_keys=True
    )


def test_selection_follows_registry_identity_across_entity_id_rename(hass) -> None:
    _install_registry(hass, _entry("stable", "light.renamed"))
    hass.states.get.return_value = _state({"brightness": 123})
    exposed = [_entity("light.renamed")]

    result = enrich_exposed_entities(
        hass,
        {CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:stable": ["brightness"]}},
        exposed,
    )

    assert result[0]["attributes"] == {"brightness": 123}
    assert "attributes" not in exposed[0], "renderer enrichment must not mutate callers"


def test_entity_id_reuse_cannot_inherit_deleted_registry_preference(hass) -> None:
    _install_registry(hass, _entry("replacement", "light.original"))
    hass.states.get.return_value = _state({"brightness": 222})
    exposed = [_entity("light.original")]

    result = enrich_exposed_entities(
        hass,
        {CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:deleted": ["brightness"]}},
        exposed,
    )

    assert result == exposed
    assert "attributes" not in result[0]


def test_saved_preference_cannot_re_expose_an_entity(hass) -> None:
    _install_registry(
        hass,
        _entry("hidden", "light.hidden"),
        _entry("visible", "light.visible"),
    )
    hass.states.get.side_effect = lambda entity_id: _state(
        {"brightness": 200 if entity_id == "light.hidden" else 100}
    )
    exposed = [_entity("light.visible", "Visible")]

    result = enrich_exposed_entities(
        hass,
        {CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:hidden": ["brightness"]}},
        exposed,
    )

    assert [item["entity_id"] for item in result] == ["light.visible"]
    assert "attributes" not in result[0]


def test_missing_attribute_is_temporarily_omitted_without_clearing_selection(hass) -> None:
    _install_registry(hass, _entry("stable", "light.kitchen"))
    hass.states.get.return_value = _state({"supported_color_modes": ["brightness"]})
    options = {
        CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:stable": ["brightness"]}
    }

    result = enrich_exposed_entities(hass, options, [_entity("light.kitchen")])

    assert "attributes" not in result[0]
    assert options[CONF_EXPOSED_ENTITY_ATTRIBUTES] == {
        "registry:stable": ["brightness"]
    }


def test_attribute_values_are_resolved_live_for_each_render(hass) -> None:
    _install_registry(hass, _entry("stable", "light.kitchen"))
    current = {"brightness": 10}
    hass.states.get.side_effect = lambda _entity_id: _state(dict(current))
    options = {
        CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:stable": ["brightness"]}
    }
    exposed = [_entity("light.kitchen")]

    first = enrich_exposed_entities(hass, options, exposed)
    current["brightness"] = 240
    second = enrich_exposed_entities(hass, options, exposed)

    assert first[0]["attributes"] == {"brightness": 10}
    assert second[0]["attributes"] == {"brightness": 240}


def test_large_single_value_is_replaced_by_bounded_marker() -> None:
    result = _safe_attribute_value("x" * 5000)

    assert isinstance(result, str)
    assert result.startswith("<omitted: ")
    assert len(result) < 100


def test_total_live_attribute_serialization_is_bounded(hass) -> None:
    _install_registry(hass, _entry("stable", "sensor.verbose"))
    attributes = {f"attribute_{index}": "x" * 4000 for index in range(12)}
    hass.states.get.return_value = _state(attributes)
    selected = sorted(attributes)

    result = enrich_exposed_entities(
        hass,
        {CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:stable": selected}},
        [_entity("sensor.verbose", "Verbose")],
    )
    emitted = result[0]["attributes"]
    cost = sum(_attribute_item_size(name, value) for name, value in emitted.items())

    assert 0 < len(emitted) < len(selected)
    assert cost <= MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS


def test_catalog_separates_current_registryless_and_inactive_preferences(
    hass, monkeypatch
) -> None:
    _install_registry(
        hass,
        _entry("current", "light.current"),
        _entry("inactive", "light.inactive"),
    )
    states = {
        "light.current": _state({"brightness": 100}),
        "sensor.registryless": _state({"unit_of_measurement": "C"}),
    }
    hass.states.get.side_effect = states.get
    monkeypatch.setattr(
        exposed_attributes,
        "get_exposed_entities",
        lambda _hass: [
            _entity("light.current", "Current"),
            _entity("sensor.registryless", "Registryless"),
        ],
    )

    catalog = exposed_attribute_catalog(
        hass,
        {
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:current": ["brightness", "missing"],
                "registry:inactive": ["brightness"],
                "registry:deleted": ["old_attribute"],
            }
        },
    )
    current = next(item for item in catalog["entities"] if item["entity_id"] == "light.current")
    registryless = next(
        item for item in catalog["entities"] if item["entity_id"] == "sensor.registryless"
    )
    inactive = {item["reference"]: item for item in catalog["saved_unexposed"]}

    assert current["durable_selection_available"] is True
    assert current["selected_attributes"] == ["brightness", "missing"]
    assert current["missing_selected_attributes"] == ["missing"]
    assert registryless["durable_selection_available"] is False
    assert registryless["reference"] is None
    assert inactive["registry:inactive"]["registry_entry_exists"] is True
    assert inactive["registry:deleted"]["registry_entry_exists"] is False


def test_maintained_default_formats_include_only_live_selected_values(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        exposed_attributes,
        "get_entity_prompt_metadata",
        lambda _hass, _entity_id: SimpleNamespace(area_id="kitchen"),
    )
    monkeypatch.setattr(exposed_attributes, "resolve_area_id", lambda *_args: "kitchen")
    entities = [
        {**_entity("light.kitchen", "Kitchen Lamp"), "attributes": {"brightness": 123}},
        _entity("switch.kettle", "Kettle"),
    ]

    grouped = _render_grouped_default_with_attributes(hass, entities)
    legacy = _render_legacy_default_with_attributes(hass, entities)

    assert "entity_id,name,state,aliases,attributes" in grouped
    assert '"{""brightness"":123}"' in grouped
    assert "switch.kettle,,on,," in grouped
    assert "entity_id,name,state,area_id,aliases,attributes" in legacy
    assert '"{""brightness"":123}"' in legacy


def test_custom_template_receives_selected_attribute_mapping(hass) -> None:
    _install_registry(hass, _entry("stable", "light.kitchen"))
    hass.states.get.return_value = _state({"brightness": 177})
    options = agent_config_defaults()
    options.update(
        {
            CONF_PROMPT: "Brightness={{ exposed_entities[0].attributes.brightness }}",
            CONF_CURRENT_DATETIME_ENABLED: False,
            CONF_EXPOSED_ENTITIES_ENABLED: False,
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:stable": ["brightness"]
            },
        }
    )
    renderer = _wrap_effective_prompt_renderer(prompt.render_effective_prompt)

    result = renderer(
        hass,
        options,
        exposed_entities=[_entity("light.kitchen")],
        current_device_id=None,
        user_input=None,
        skills=[],
    )

    assert result.text == "Brightness=177"


@pytest.mark.asyncio
async def test_preview_counts_rendered_attribute_context(hass, monkeypatch) -> None:
    _install_registry(hass, _entry("stable", "light.kitchen"))
    hass.states.get.return_value = _state({"brightness": 188})
    options = agent_config_defaults()
    options.update(
        {
            CONF_PROMPT: "Brightness={{ exposed_entities[0].attributes.brightness }}",
            CONF_CURRENT_DATETIME_ENABLED: False,
            CONF_EXPOSED_ENTITIES_ENABLED: False,
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:stable": ["brightness"]
            },
        }
    )
    monkeypatch.setattr(management_ui, "get_exposed_entities", lambda _hass: [_entity("light.kitchen")])
    monkeypatch.setattr(
        management_ui,
        "render_effective_prompt",
        _wrap_effective_prompt_renderer(prompt.render_effective_prompt),
    )

    result = await _async_preview_effective_prompt(
        hass,
        SimpleNamespace(entry_id="entry-1", data={}),
        SimpleNamespace(subentry_id="agent-1"),
        options,
        "admin",
    )
    system = next(section for section in result["sections"] if section["key"] == "system_context")

    assert "Brightness=188" in result["prompt"]
    assert system["character_count"] == len(system["content"])
    assert result["total_character_count"] >= system["character_count"]


def test_runtime_installer_wraps_every_copied_effective_renderer_reference() -> None:
    from custom_components.extended_openai_conversation_responses import conversation

    saved = {
        "installed": exposed_attributes._INSTALLED,
        "default": prompt._default_exposed_entities_context,
        "template": prompt._render_template,
        "prompt": prompt.render_effective_prompt,
        "conversation": conversation.render_effective_prompt,
        "management": management_ui.render_effective_prompt,
        "command": management_ui.async_management_command,
        "modules": management_ui.MANAGEMENT_FRONTEND_MODULES,
    }
    try:
        exposed_attributes._INSTALLED = False
        exposed_attributes.install_exposed_attribute_runtime()

        assert prompt.render_effective_prompt is not saved["prompt"]
        assert conversation.render_effective_prompt is not saved["conversation"]
        assert management_ui.render_effective_prompt is not saved["management"]
        assert "exposed-attributes-ui.js" in management_ui.MANAGEMENT_FRONTEND_MODULES
    finally:
        prompt._default_exposed_entities_context = saved["default"]
        prompt._render_template = saved["template"]
        prompt.render_effective_prompt = saved["prompt"]
        conversation.render_effective_prompt = saved["conversation"]
        management_ui.render_effective_prompt = saved["management"]
        management_ui.async_management_command = saved["command"]
        management_ui.MANAGEMENT_FRONTEND_MODULES = saved["modules"]
        exposed_attributes._INSTALLED = saved["installed"]
