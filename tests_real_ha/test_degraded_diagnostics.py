"""Real-HA acceptance for diagnostics from a partially degraded loaded agent."""

from __future__ import annotations

from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import conversation as runtime
from custom_components.extended_openai_conversation_responses.const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_TEMPORARY_MEMORY,
    SUBSYSTEM_STATUS_KEY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

_DIAGNOSTIC_MEMORY = "diagnostic secret launch phrase is cobalt-orchid"
_OWNER_SCOPE = "user:diagnostics-owner"


@pytest.mark.asyncio
async def test_diagnostics_remain_safe_and_useful_when_optional_subsystem_failed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded optional subsystem must not break diagnostics or the live agent."""

    async def broken_knowledge(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("simulated knowledge initialization failure")

    # Fail one optional subsystem during the real conversation entity lifecycle.
    # The entity deliberately catches this failure and remains usable.
    monkeypatch.setattr(runtime, "async_get_knowledge", broken_knowledge)

    entry = _make_entry(
        "Degraded Diagnostics",
        include_ai_task=False,
        local_intents=True,
        conversation_options={
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._knowledge is None
    assert agent._temporary_memory is not None

    subentry = agent.subentry
    subsystem_status = hass.data[SUBSYSTEM_STATUS_KEY][
        (entry.entry_id, subentry.subentry_id)
    ]
    assert subsystem_status["knowledge"] == {
        "configured": True,
        "status": "failed",
        "error_type": "RuntimeError",
    }
    assert subsystem_status["temporary_memory"]["status"] == "healthy"

    # Put real user content into another healthy optional subsystem. Diagnostics
    # should expose only aggregate counters, never the stored text itself.
    created = await agent._temporary_memory.async_add(
        _OWNER_SCOPE,
        _DIAGNOSTIC_MEMORY,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=_OWNER_SCOPE,
    )
    assert created["status"] == "created"

    # The still-loaded conversation agent must remain healthy despite Knowledge
    # being unavailable. A built-in local intent proves this without provider I/O.
    provider_path = AsyncMock(
        side_effect=AssertionError(
            "A local intent unexpectedly reached the provider after subsystem failure"
        )
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)
    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    provider_path.assert_not_awaited()
    assert result.response.as_dict()["speech"]["plain"]["speech"]

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert list(diagnostics) == ["conversation_agents"]
    assert len(diagnostics["conversation_agents"]) == 1
    agent_diagnostics = diagnostics["conversation_agents"][0]

    # Diagnostics must preserve the initialization health signal instead of trying
    # to recreate a failed subsystem or failing the whole diagnostics request.
    assert agent_diagnostics["optional_subsystems"]["knowledge"] == {
        "configured": True,
        "status": "failed",
        "error_type": "RuntimeError",
    }
    assert agent_diagnostics["knowledge_storage_error"] == "RuntimeError"
    assert agent_diagnostics["optional_subsystems"]["temporary_memory"][
        "status"
    ] == "healthy"
    assert agent_diagnostics["active_temporary_memory_count"] == 1

    # Diagnostics are support metadata, not a data export. Check the serialized
    # payload so nested fields cannot accidentally expose credentials or memory.
    serialized = json.dumps(diagnostics, sort_keys=True)
    assert _DIAGNOSTIC_MEMORY not in serialized
    assert "sk-acceptance-test" not in serialized
    assert "simulated knowledge initialization failure" not in serialized

    # Collecting diagnostics must itself be side-effect free for the degraded
    # subsystem and leave the same live conversation agent registered.
    assert agent._knowledge is None
    assert conversation.async_get_agent(hass, entry.entry_id) is agent
    assert entry.state is ConfigEntryState.LOADED
