"""Knowledge management lifecycle acceptance through real HA and provider wire."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _chat_tool_result,
    _say,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire

_QUERY = "lifecycle reference marker"
_OLD_MARKER = "atlas-copper"
_NEW_MARKER = "atlas-indigo"
_CREATE_CALL = "call-knowledge-live-create"
_UPDATE_CALL = "call-knowledge-live-update"
_DISABLE_CALL = "call-knowledge-live-disable"
_REENABLE_CALL = "call-knowledge-live-reenable"
_DELETE_CALL = "call-knowledge-live-delete"


def _search_results(wire: Any, call_id: str) -> list[dict[str, Any]]:
    """Decode one Knowledge search result by exact provider tool-call identity."""
    for request in wire.requests:
        body = request["body"]
        if any(
            item.get("role") == "tool" and item.get("tool_call_id") == call_id
            for item in body.get("messages", [])
        ):
            result = _chat_tool_result(body, call_id)
            return result["results"]
    raise AssertionError(
        f"Provider request containing Knowledge tool result {call_id!r} was not captured"
    )


@pytest.mark.asyncio
async def test_management_mutations_update_loaded_agent_knowledge_immediately(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """Join real management CRUD to the same loaded agent and provider boundary."""
    entry = _make_entry(
        "Knowledge Lifecycle Acceptance",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._knowledge is not None

    client = await _admin_client(hass, hass_ws_client)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                _CREATE_CALL,
                "knowledge_search",
                {"query": _QUERY, "limit": 5},
            ),
            _chat_sse_text("The created Knowledge source is available."),
            _chat_sse_tool_call(
                _UPDATE_CALL,
                "knowledge_search",
                {"query": _QUERY, "limit": 5},
            ),
            _chat_sse_text("The updated Knowledge source is available."),
            _chat_sse_tool_call(
                _DISABLE_CALL,
                "knowledge_search",
                {"query": _QUERY, "limit": 5},
            ),
            _chat_sse_text("The disabled Knowledge source is unavailable."),
            _chat_sse_tool_call(
                _REENABLE_CALL,
                "knowledge_search",
                {"query": _QUERY, "limit": 5},
            ),
            _chat_sse_text("The re-enabled Knowledge source is available."),
            _chat_sse_tool_call(
                _DELETE_CALL,
                "knowledge_search",
                {"query": _QUERY, "limit": 5},
            ),
            _chat_sse_text("The deleted Knowledge source is unavailable."),
        ],
    )

    created = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="create",
        title="Runtime lifecycle handbook",
        description="Knowledge management lifecycle reference",
        content=f"The lifecycle reference marker is {_OLD_MARKER}.",
        enabled=True,
    )
    source_id = created["source"]["source_id"]

    await _say(hass, agent, "Find the lifecycle reference marker.")
    created_results = _search_results(wire, _CREATE_CALL)
    assert len(created_results) == 1
    assert created_results[0]["source_id"] == source_id
    assert _OLD_MARKER in created_results[0]["excerpt"]

    updated = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="update",
        source_id=source_id,
        content=f"The lifecycle reference marker is {_NEW_MARKER}.",
    )
    assert updated["source"]["source_id"] == source_id

    await _say(hass, agent, "Find the lifecycle reference marker again.")
    updated_results = _search_results(wire, _UPDATE_CALL)
    assert len(updated_results) == 1
    assert updated_results[0]["source_id"] == source_id
    assert _NEW_MARKER in updated_results[0]["excerpt"]
    assert _OLD_MARKER not in updated_results[0]["excerpt"]

    disabled = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="update",
        source_id=source_id,
        enabled=False,
    )
    assert disabled["source"]["enabled"] is False

    await _say(hass, agent, "Search for the lifecycle reference marker.")
    assert _search_results(wire, _DISABLE_CALL) == []

    listed = await _management_call(
        client, entry=entry, section="knowledge", action="list"
    )
    stored = next(source for source in listed["sources"] if source["source_id"] == source_id)
    assert stored["enabled"] is False

    reenabled = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="update",
        source_id=source_id,
        enabled=True,
    )
    assert reenabled["source"]["enabled"] is True

    await _say(hass, agent, "Search after re-enabling the lifecycle reference marker.")
    reenabled_results = _search_results(wire, _REENABLE_CALL)
    assert len(reenabled_results) == 1
    assert reenabled_results[0]["source_id"] == source_id
    assert _NEW_MARKER in reenabled_results[0]["excerpt"]
    assert _OLD_MARKER not in reenabled_results[0]["excerpt"]

    deleted = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="delete",
        source_id=source_id,
        confirm=True,
    )
    assert deleted["deleted"] == 1

    await _say(hass, agent, "Search once more for the lifecycle reference marker.")
    assert _search_results(wire, _DELETE_CALL) == []

    after_delete = await _management_call(
        client, entry=entry, section="knowledge", action="list"
    )
    assert all(source["source_id"] != source_id for source in after_delete["sources"])

    # Knowledge is stored outside config-entry options: management mutations must be
    # visible to the already-loaded agent without an integration reload.
    assert conversation.async_get_agent(hass, entry.entry_id) is agent
