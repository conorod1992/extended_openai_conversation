"""Focused coverage for exposed-attribute backend edge cases."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import exposed_attributes as ea


class _States:
    def __init__(self, states: dict[str, Any]) -> None:
        self._states = states

    def get(self, entity_id: str) -> Any:
        return self._states.get(entity_id)


def _hass(states: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(states=_States(states or {}), data={})


def test_validate_preferences_canonicalizes_and_deduplicates() -> None:
    assert ea._validate_preferences(
        {
            "registry:b": ["z", "a", "z"],
            "registry:a": [],
        }
    ) == {"registry:b": ["a", "z"]}
    assert ea._validate_preferences(None) == {}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "must be an object"),
        ({"light.kitchen": ["brightness"]}, "stable Home Assistant registry ID"),
        ({"registry: entry": ["brightness"]}, "stable Home Assistant registry ID"),
        ({"registry:id": "brightness"}, "must be a list of attribute names"),
        ({"registry:id": [""]}, "attribute names must be non-empty strings"),
        ({"registry:id": [" brightness"]}, "attribute names must be non-empty strings"),
        ({"registry:id": [1]}, "attribute names must be non-empty strings"),
    ],
)
def test_validate_preferences_rejects_malformed_values(
    value: Any, message: str
) -> None:
    with pytest.raises(ea.agent_config.AgentConfigError, match=message):
        ea._validate_preferences(value)


def test_validate_preferences_enforces_configured_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ea, "MAX_CONFIGURED_ENTITIES", 1)
    with pytest.raises(ea.agent_config.AgentConfigError, match="at most 1 entities"):
        ea._validate_preferences({"registry:a": ["x"], "registry:b": ["y"]})

    monkeypatch.setattr(ea, "MAX_ATTRIBUTES_PER_ENTITY", 1)
    with pytest.raises(ea.agent_config.AgentConfigError, match="at most 1 attributes"):
        ea._validate_preferences({"registry:a": ["x", "y"]})

    monkeypatch.setattr(ea, "MAX_ATTRIBUTE_NAME_LENGTH", 2)
    with pytest.raises(ea.agent_config.AgentConfigError, match="attribute names"):
        ea._validate_preferences({"registry:a": ["long"]})


def test_normalizer_delegates_non_dict_and_handles_optional_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, bool, bool]] = []

    def original(data: Any, *, apply_defaults: bool, reject_unknown: bool) -> dict[str, Any]:
        calls.append((data, apply_defaults, reject_unknown))
        return {"base": True}

    monkeypatch.setattr(ea, "_ORIGINAL_NORMALIZE_AGENT_CONFIG", original)

    assert ea._normalize_agent_config_with_exposed_attributes(
        "legacy", apply_defaults=False, reject_unknown=False
    ) == {"base": True}
    assert calls[-1] == ("legacy", False, False)

    assert ea._normalize_agent_config_with_exposed_attributes(
        {ea.CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:id": ["brightness"]}},
        apply_defaults=False,
    ) == {
        "base": True,
        ea.CONF_EXPOSED_ENTITY_ATTRIBUTES: {"registry:id": ["brightness"]},
    }


def test_registry_lookup_supports_current_and_legacy_container_shapes() -> None:
    entry = SimpleNamespace(id="entry-1", entity_id="light.kitchen")
    current = SimpleNamespace(entities=SimpleNamespace(get_entry=lambda entry_id: entry if entry_id == "entry-1" else None))
    legacy = SimpleNamespace(entities=SimpleNamespace(values=lambda: [entry]))
    unsupported = SimpleNamespace(entities=SimpleNamespace())

    assert ea._registry_entry_by_id(current, "entry-1") is entry
    assert ea._registry_entry_by_id(legacy, "entry-1") is entry
    assert ea._registry_entry_by_id(legacy, "missing") is None
    assert ea._registry_entry_by_id(unsupported, "entry-1") is None

    registry = SimpleNamespace(async_get=lambda entity_id: entry if entity_id == "light.kitchen" else None, entities=current.entities)
    assert ea._reference_for_entity(registry, "light.kitchen") == "registry:entry-1"
    assert ea._reference_for_entity(registry, "light.missing") is None
    assert ea._entry_for_reference(registry, "entity:entry-1") is None
    assert ea._entry_for_reference(registry, "registry:entry-1") is entry


def test_preferences_from_options_fails_closed() -> None:
    assert ea._preferences_from_options(object()) == {}
    assert ea._preferences_from_options({ea.CONF_EXPOSED_ENTITY_ATTRIBUTES: "bad"}) == {}


def test_exposed_attribute_catalog_keeps_stale_saved_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exposed_entry = SimpleNamespace(id="one", entity_id="light.kitchen")
    stale_entry = SimpleNamespace(id="two", entity_id="sensor.old")
    entries = {"one": exposed_entry, "two": stale_entry}
    registry = SimpleNamespace(
        async_get=lambda entity_id: exposed_entry if entity_id == "light.kitchen" else None,
        entities=SimpleNamespace(get_entry=lambda entry_id: entries.get(entry_id)),
    )
    hass = _hass(
        {
            "light.kitchen": SimpleNamespace(attributes={"brightness": 100, "effect": "none"}),
        }
    )
    monkeypatch.setattr(ea.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        ea,
        "get_exposed_entities",
        lambda _hass: [
            {"entity_id": "light.kitchen", "name": "Kitchen"},
            {"entity_id": "light.gone", "name": "Gone"},
        ],
    )

    result = ea.exposed_attribute_catalog(
        hass,
        {
            ea.CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:one": ["brightness", "missing"],
                "registry:two": ["unit_of_measurement"],
                "registry:missing": ["state_class"],
            }
        },
    )

    assert result["entities"] == [
        {
            "entity_id": "light.kitchen",
            "name": "Kitchen",
            "reference": "registry:one",
            "attributes": ["brightness", "effect"],
            "selected_attributes": ["brightness", "missing"],
            "missing_selected_attributes": ["missing"],
            "durable_selection_available": True,
        }
    ]
    assert result["saved_unexposed"] == [
        {
            "reference": "registry:two",
            "entity_id": "sensor.old",
            "name": "sensor.old",
            "selected_attributes": ["unit_of_measurement"],
            "registry_entry_exists": True,
        },
        {
            "reference": "registry:missing",
            "entity_id": None,
            "name": "Unavailable entity",
            "selected_attributes": ["state_class"],
            "registry_entry_exists": False,
        },
    ]


def test_safe_attribute_value_handles_cycles_limits_and_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recursive: list[Any] = []
    recursive.append(recursive)
    assert ea._safe_attribute_value(recursive) == "[[...]]"

    monkeypatch.setattr(ea, "MAX_ATTRIBUTE_VALUE_CHARACTERS", 4)
    assert str(ea._safe_attribute_value("long value")).startswith("<omitted:")

    monkeypatch.setattr(ea, "MAX_ATTRIBUTE_VALUE_CHARACTERS", 4096)
    original_loads = ea.json.loads
    monkeypatch.setattr(
        ea.json,
        "loads",
        lambda _value: (_ for _ in ()).throw(ea.json.JSONDecodeError("bad", "x", 0)),
    )
    assert ea._safe_attribute_value("value") == "value"
    monkeypatch.setattr(ea.json, "loads", original_loads)


def test_enrich_exposed_entities_handles_stale_state_and_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        "one": SimpleNamespace(entity_id="light.kitchen"),
        "two": SimpleNamespace(entity_id="light.missing"),
        "three": SimpleNamespace(entity_id="light.not_exposed"),
    }
    registry = SimpleNamespace(
        entities=SimpleNamespace(get_entry=lambda entry_id: entries.get(entry_id))
    )
    monkeypatch.setattr(ea.er, "async_get", lambda _hass: registry)
    hass = _hass({"light.kitchen": SimpleNamespace(attributes={"brightness": 123, "effect": "rainbow"})})
    exposed = [
        {"entity_id": "light.kitchen", "name": "Kitchen"},
        {"entity_id": "light.missing", "name": "Missing"},
        {"entity_id": "switch.other", "name": "Other"},
    ]
    options = {
        ea.CONF_EXPOSED_ENTITY_ATTRIBUTES: {
            "registry:one": ["brightness", "not_present"],
            "registry:two": ["anything"],
            "registry:three": ["ignored"],
        }
    }

    result = ea.enrich_exposed_entities(hass, options, exposed)
    assert result[0]["attributes"] == {"brightness": 123}
    assert result[1] is exposed[1]
    assert result[2] is exposed[2]

    monkeypatch.setattr(ea, "MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS", 0)
    result = ea.enrich_exposed_entities(hass, options, exposed)
    assert "attributes" not in result[0]


def test_enrich_exposed_entities_fast_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    exposed = [{"entity_id": "light.kitchen"}]
    assert ea.enrich_exposed_entities(_hass(), {}, exposed) is exposed

    monkeypatch.setattr(ea, "_preferences_from_options", lambda _options: {"registry:x": ["brightness"]})
    registry = SimpleNamespace(entities=SimpleNamespace(get_entry=lambda _entry_id: None))
    monkeypatch.setattr(ea.er, "async_get", lambda _hass: registry)
    assert ea.enrich_exposed_entities(_hass(), {}, exposed) is exposed


def test_attribute_helpers_and_renderers_cover_empty_and_non_string_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ea._attributes_json({}) == ""
    assert ea._attributes_json({"attributes": {}}) == "{}"
    assert ea._attribute_context_size({}) == 0
    assert ea._attribute_item_size("x", 1) > 0

    monkeypatch.setattr(ea, "resolve_area_id", lambda _hass, entity_id: f"area-{entity_id}")
    rendered = ea._render_legacy_default(
        object(),
        [{"entity_id": "light.kitchen", "name": "Kitchen", "state": "on", "aliases": ["K"], "attributes": {"brightness": 1}}],
        include_attributes=True,
    )
    assert "attributes" in rendered
    assert '"{""brightness"":1}"' in rendered


def test_effective_prompt_wrapper_enriches_only_explicit_entity_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def original(_hass: Any, _options: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("exposed_entities"))
        return "rendered"

    monkeypatch.setattr(
        ea,
        "enrich_exposed_entities",
        lambda _hass, _options, entities: [*entities, {"entity_id": "sensor.extra"}],
    )
    wrapped = ea._wrap_effective_prompt_renderer(original)

    assert wrapped(object(), {}, exposed_entities=[{"entity_id": "light.one"}]) == "rendered"
    assert calls[-1][-1]["entity_id"] == "sensor.extra"
    assert wrapped(object(), {}, exposed_entities="not-a-list") == "rendered"
    assert calls[-1] == "not-a-list"


def test_configuration_result_decoration_and_install_idempotence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    untouched = {"config": "invalid"}
    assert ea._decorate_configuration_result(object(), untouched) is untouched

    monkeypatch.setattr(ea, "exposed_attribute_catalog", lambda _hass, config: {"seen": config})
    result = ea._decorate_configuration_result(object(), {"config": {"x": 1}, "other": True})
    assert result == {
        "config": {"x": 1},
        "other": True,
        "exposed_attribute_catalog": {"seen": {"x": 1}},
    }

    monkeypatch.setattr(ea, "_INSTALLED", True)
    ea.install_exposed_attribute_runtime()
