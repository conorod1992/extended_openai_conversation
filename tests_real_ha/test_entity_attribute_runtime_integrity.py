"""Real-HA acceptance for selected exposed-entity attribute runtime integrity."""

from __future__ import annotations

import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_EXPOSED_ENTITIES_ENABLED,
)
from custom_components.extended_openai_conversation_responses.exposed_attributes import (
    CONF_EXPOSED_ENTITY_ATTRIBUTES,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _speech,
)

_ATTRIBUTE = "runtime_marker"
_FIRST = "attribute-alpha-live"
_SECOND = "attribute-beta-live"
_THIRD = "attribute-gamma-live"


def _provider_text(body: dict[str, Any]) -> str:
    """Return one stable serialized view of the real SDK request body."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


async def _registered_exposed_sensor(hass: HomeAssistant) -> Any:
    """Create one genuine registry-backed entity and expose it to Assist."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "sensor",
        "entity_attribute_runtime_test",
        "selected-attribute-runtime",
        suggested_object_id="selected_attribute_runtime",
    )
    hass.states.async_set(
        entry.entity_id,
        "ready",
        {_ATTRIBUTE: _FIRST, "unselected_marker": "must-not-be-enriched"},
    )
    async_expose_entity(hass, conversation.DOMAIN, entry.entity_id, True)
    return entry


async def _agent(hass: HomeAssistant, api_mode: str, registry_entry_id: str):
    """Load a genuine agent configured with one stable attribute preference."""
    entry = _make_entry(
        "Entity Attribute Runtime",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_EXPOSED_ENTITIES_ENABLED: True,
            CONF_EXPOSED_ENTITY_ATTRIBUTES: {
                f"registry:{registry_entry_id}": [_ATTRIBUTE]
            },
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return agent


async def _say(
    hass: HomeAssistant, agent: Any, text: str = "Inspect the currently exposed entities."
):
    """Enter through Home Assistant's public conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


async def test_selected_attribute_is_live_missing_safe_and_exposure_bound(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Prove live refresh and fail-closed exposure semantics at provider boundary."""
    entity = await _registered_exposed_sensor(hass)
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS, entity.id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("OK") for _ in range(5)],
    )

    first = await _say(hass, agent)
    assert _speech(first) == "OK"
    first_body = _provider_text(wire.requests[0]["body"])
    assert _FIRST in first_body
    assert "must-not-be-enriched" not in first_body

    # Change only attributes after setup. The next request must resolve the new live
    # value rather than reuse anything captured when the agent was loaded.
    hass.states.async_set(entity.entity_id, "ready", {_ATTRIBUTE: _SECOND})
    second = await _say(hass, agent)
    assert _speech(second) == "OK"
    second_body = _provider_text(wire.requests[1]["body"])
    assert _SECOND in second_body
    assert _FIRST not in second_body

    # A selected attribute can disappear temporarily without leaking a stale value.
    hass.states.async_set(entity.entity_id, "ready", {"other": "still-present"})
    third = await _say(hass, agent)
    assert _speech(third) == "OK"
    third_body = _provider_text(wire.requests[2]["body"])
    assert _FIRST not in third_body
    assert _SECOND not in third_body

    # Saving a preference must never broaden HA exposure. While unexposed, even a
    # newly changed selected value stays out of the provider request.
    hass.states.async_set(entity.entity_id, "ready", {_ATTRIBUTE: _THIRD})
    async_expose_entity(hass, conversation.DOMAIN, entity.entity_id, False)
    fourth = await _say(hass, agent)
    assert _speech(fourth) == "OK"
    fourth_body = _provider_text(wire.requests[3]["body"])
    assert _THIRD not in fourth_body

    # The durable registry-backed selection becomes effective again when HA exposure
    # is restored; no agent reload or preference rewrite is required.
    async_expose_entity(hass, conversation.DOMAIN, entity.entity_id, True)
    fifth = await _say(hass, agent)
    assert _speech(fifth) == "OK"
    fifth_body = _provider_text(wire.requests[4]["body"])
    assert _THIRD in fifth_body

    assert [request["path"] for request in wire.requests] == [
        "/v1/chat/completions"
    ] * 5


async def test_selected_attribute_runtime_bounding_reaches_both_provider_apis(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Prove bounded live values reach Chat and Responses without mutating HA state."""
    entity = await _registered_exposed_sensor(hass)
    oversized = "z" * 5000
    hass.states.async_set(entity.entity_id, "ready", {_ATTRIBUTE: oversized})

    for api_mode, reply, path in (
        (API_MODE_CHAT_COMPLETIONS, _chat_sse_text("OK"), "/v1/chat/completions"),
        (API_MODE_RESPONSES, _responses_sse_text("OK"), "/v1/responses"),
    ):
        agent = await _agent(hass, api_mode, entity.id)
        wire = _install_wire(monkeypatch, agent, [reply])

        result = await _say(hass, agent)
        assert _speech(result) == "OK"
        assert [request["path"] for request in wire.requests] == [path]

        body = _provider_text(wire.requests[0]["body"])
        assert "<omitted:" in body
        assert "z" * 100 not in body

        live = hass.states.get(entity.entity_id)
        assert live is not None
        assert live.attributes[_ATTRIBUTE] == oversized
