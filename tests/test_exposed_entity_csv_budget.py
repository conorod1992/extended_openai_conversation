"""Regression tests for safe exposed-device CSV rendering and prompt budgets."""

from __future__ import annotations

import csv
from io import StringIO
from types import SimpleNamespace

from homeassistant.helpers import entity_registry as er

from custom_components.extended_openai_conversation_responses import exposed_attributes
from custom_components.extended_openai_conversation_responses.exposed_attributes import (
    CONF_EXPOSED_ENTITY_ATTRIBUTES,
    MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS,
    _attribute_context_size,
    _csv_row,
    _render_grouped_default,
    _render_legacy_default,
    enrich_exposed_entities,
)


def _install_registry(hass, entry_id: str, entity_id: str) -> None:
    entry = SimpleNamespace(id=entry_id, entity_id=entity_id)
    hass.data[er.DATA_REGISTRY] = SimpleNamespace(
        async_get=lambda requested: entry if requested == entity_id else None,
        entities=SimpleNamespace(
            get_entry=lambda requested: entry if requested == entry_id else None,
            values=lambda: [entry],
        ),
    )


def test_csv_row_round_trips_commas_quotes_and_line_breaks() -> None:
    fields = [
        "light.kitchen",
        'Kitchen, "secondary"\nupstairs',
        "on\rready",
        'Main/Alias, two/Quote "three"',
        '{"note":"line\\nvalue,with comma"}',
    ]

    rendered = _csv_row(fields)

    assert next(csv.reader(StringIO(rendered))) == fields


def test_grouped_default_uses_csv_writer_without_attributes(hass, monkeypatch) -> None:
    monkeypatch.setattr(
        exposed_attributes,
        "get_entity_prompt_metadata",
        lambda _hass, _entity_id: SimpleNamespace(area_id="kitchen"),
    )
    entities = [
        {
            "entity_id": "light.kitchen",
            "name": 'Kitchen, "main"',
            "state": "on",
            "aliases": ["Ceiling, primary"],
        }
    ]

    rendered = _render_grouped_default(hass, entities, include_attributes=False)
    lines = rendered.splitlines()

    assert next(csv.reader([lines[1]])) == ["entity_id", "name", "state", "aliases"]
    assert lines[2] == "area_id=kitchen"
    assert next(csv.reader([lines[3]])) == [
        "light.kitchen",
        'Kitchen, "main"',
        "on",
        "Ceiling, primary",
    ]


def test_legacy_default_quotes_fields_and_passes_raw_json_to_writer(hass, monkeypatch) -> None:
    monkeypatch.setattr(exposed_attributes, "resolve_area_id", lambda *_args: "kitchen")
    entities = [
        {
            "entity_id": "light.kitchen",
            "name": "Kitchen, main",
            "state": 'on, "bright"',
            "aliases": ["Ceiling, primary"],
            "attributes": {"note": 'value, "quoted"'},
        }
    ]

    rendered = _render_legacy_default(hass, entities, include_attributes=True)
    lines = rendered.splitlines()

    assert next(csv.reader([lines[2]])) == [
        "entity_id",
        "name",
        "state",
        "area_id",
        "aliases",
        "attributes",
    ]
    assert next(csv.reader([lines[3]])) == [
        "light.kitchen",
        "Kitchen, main",
        'on, "bright"',
        "kitchen",
        "Ceiling, primary",
        '{"note":"value, \\"quoted\\""}',
    ]


def test_aggregate_budget_counts_final_csv_encoded_attribute_cell(hass) -> None:
    entity_id = "sensor.verbose"
    _install_registry(hass, "stable", entity_id)
    attributes = {f"attribute_{index}": '"' * 300 for index in range(64)}
    hass.states.get.return_value = SimpleNamespace(attributes=attributes)

    result = enrich_exposed_entities(
        hass,
        {
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                "registry:stable": sorted(attributes),
            }
        },
        [
            {
                "entity_id": entity_id,
                "name": "Verbose",
                "state": "on",
                "aliases": [],
            }
        ],
    )
    emitted = result[0]["attributes"]

    assert 0 < len(emitted) < len(attributes)
    assert _attribute_context_size(emitted) <= MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS

    next_name = next(name for name in sorted(attributes) if name not in emitted)
    assert (
        _attribute_context_size({**emitted, next_name: attributes[next_name]})
        > MAX_TOTAL_ATTRIBUTE_CONTEXT_CHARACTERS
    )
