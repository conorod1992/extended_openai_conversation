"""Real-HA acceptance for live model-catalog refresh during an active turn."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_reasoning_effort_options,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    BUNDLED_CATALOG,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_backup_transfer_protocol import _user_token

_WAIT_TIMEOUT = 10


async def _say(
    hass: HomeAssistant,
    entry_id: str,
    text: str,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public Conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


def _speech(result: conversation.ConversationResult) -> str:
    """Return plain speech from a successful conversation result."""
    assert result.response.error_code is None
    return result.response.as_dict()["speech"]["plain"]["speech"]


@pytest.mark.asyncio
async def test_catalog_refresh_publishes_live_while_existing_agent_turn_is_active(
    hass: HomeAssistant,
    hass_ws_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog updates become live without replacing or poisoning an active agent."""
    entry = _make_entry("Live Catalog Refresh", include_ai_task=False)
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    initial_efforts = get_reasoning_effort_options("gpt-5.6")
    assert "minimal" not in initial_efforts

    updated_catalog = deepcopy(BUNDLED_CATALOG)
    updated_catalog["catalog_version"] += 1
    updated_model = next(
        item for item in updated_catalog["models"] if item["id"] == "gpt-5.6"
    )
    updated_model["reasoning"]["efforts"].append("minimal")
    updated_model["reasoning"]["by_api"]["responses"]["efforts"].append("minimal")

    async def chunks(_size: int):
        yield json.dumps(updated_catalog).encode()

    context = AsyncMock()
    context.__aenter__.return_value = SimpleNamespace(
        status=200,
        headers={"ETag": '"live-catalog-refresh"'},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime,
        "async_get_clientsession",
        lambda _: SimpleNamespace(get=get),
    )

    admin = await hass_ws_client(
        hass,
        await _user_token(
            hass,
            MockUser(id="live-catalog-admin", is_owner=True),
        ),
    )

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    capabilities_seen: list[list[str]] = []

    async def model(log, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        capabilities_seen.append(get_reasoning_effort_options("gpt-5.6"))
        if calls == 1:
            entered.set()
            await release.wait()
            reply = "The active turn survived the catalog refresh."
        else:
            reply = "The same agent sees the refreshed catalog."
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=agent.entity_id, content=reply)
        )
        return None

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    first_task = asyncio.create_task(
        _say(hass, entry.entry_id, "Hold this conversation open")
    )
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert capabilities_seen == [initial_efforts]

    # Check through the actual registered admin WebSocket command while the
    # already-loaded agent is still processing its first public Assist turn.
    await admin.send_json_auto_id(
        {
            "type": runtime.WS_CATALOG,
            "action": "check",
            "model": "gpt-5.6",
        }
    )
    checked = await admin.receive_json()
    assert checked["success"] is True
    assert checked["result"]["source"] == "bundled"
    assert checked["result"]["update_available"] is True
    assert checked["result"]["reasoning_effort_options"] == initial_efforts
    assert get_reasoning_effort_options("gpt-5.6") == initial_efforts

    # Explicit application publishes the staged catalogue immediately without
    # replacing the already-loaded conversation agent.
    await admin.send_json_auto_id(
        {
            "type": runtime.WS_CATALOG,
            "action": "apply",
            "model": "gpt-5.6",
        }
    )
    updated = await admin.receive_json()
    assert updated["success"] is True
    assert updated["result"]["source"] == "downloaded"
    assert updated["result"]["update_available"] is False
    assert updated["result"]["reasoning_effort_options"][-1] == "minimal"
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "minimal"

    # A catalogue publication must not reload or replace the config entry's live
    # conversation agent merely because its capability metadata changed.
    assert conversation.async_get_agent(hass, entry.entry_id) is agent
    assert not first_task.done()

    release.set()
    first = await asyncio.wait_for(first_task, timeout=_WAIT_TIMEOUT)
    assert _speech(first) == "The active turn survived the catalog refresh."

    # The next turn goes through that exact same entity instance and reads the
    # newly activated capability catalogue immediately, without entry reload.
    second = await _say(hass, entry.entry_id, "Use the refreshed model metadata")
    assert _speech(second) == "The same agent sees the refreshed catalog."
    assert conversation.async_get_agent(hass, entry.entry_id) is agent
    assert calls == 2
    assert capabilities_seen[0] == initial_efforts
    assert capabilities_seen[1][-1] == "minimal"

    # The management surface and fresh manager both agree with the runtime view.
    await admin.send_json_auto_id(
        {
            "type": runtime.WS_CATALOG,
            "action": "lookup",
            "model": "gpt-5.6",
        }
    )
    lookup = await admin.receive_json()
    assert lookup["success"] is True
    assert lookup["result"]["reasoning_effort_options"][-1] == "minimal"

    restarted = runtime.ModelCatalogManager(hass)
    await restarted.async_load()
    assert restarted.catalog == updated_catalog
    assert restarted.available_catalog is None
    assert restarted.etag == '"live-catalog-refresh"'

    # Restore the process-global active catalogue for test isolation while leaving
    # the persisted update behavior itself covered above.
    manager: runtime.ModelCatalogManager = hass.data[runtime.DATA_MANAGER]
    reset = await manager.async_reset()
    assert reset["source"] == "bundled"
