"""Real-HA isolation when a deleted conversation subentry title is reused."""

from __future__ import annotations

from datetime import timedelta
import json
from types import MappingProxyType
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    CONF_REASONING_EFFORT,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPORARY_MEMORY,
    CONFIG_ENTRY_VERSION,
    CONVERSATION_CONTINUITY_USER,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    get_function_group_runtime,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _setup_entry, _subentry
from tests_real_ha.test_cross_feature_acceptance import _provider, _speech

_USER_ID = "same-title-recreation-owner"
_TITLE = "Reusable Conversation"
_MEMORY_MARKER = "Deleted generation durable marker amber-cipher."
_TEMPORARY_MARKER = "Deleted generation temporary marker teal-comet."
_ARCHIVE_MARKER = "Deleted generation archive marker violet-orbit."
_GROUP_ID = "same-title-old-generation-group"
_GROUP_TOOL = "same_title_generation_tool"


def _template_tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": _GROUP_TOOL,
            "description": "Return the same-title recreation acceptance marker.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "same-title-result"},
        "enabled": True,
    }


def _options() -> dict[str, Any]:
    return {
        CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        CONF_CHAT_MODEL: "gpt-5.6",
        CONF_REASONING_EFFORT: "none",
        CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
        CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        CONF_ARCHIVE_ENABLED: True,
        CONF_FUNCTION_TOOLS: [_template_tool()],
        CONF_FUNCTION_GROUPS: [
            {
                "id": _GROUP_ID,
                "name": "Same-title generation group",
                "description": "Acceptance-only on-demand function group.",
                "loading_mode": "on_demand",
                "functions": [_GROUP_TOOL],
                "enabled": True,
            }
        ],
    }


def _entry() -> MockConfigEntry:
    """Create one real parent entry whose initial conversation uses the reused title."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Same Title Recreation",
        data={
            CONF_API_KEY: "sk-same-title-recreation-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[_subentry("conversation", _TITLE, _options())],
    )


def _conversation_row(hass: HomeAssistant, entry_id: str, subentry_id: str):
    return next(
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if row.config_subentry_id == subentry_id and row.domain == conversation.DOMAIN
    )


def _tool_names(request: dict[str, Any]) -> set[str]:
    return {
        item["function"]["name"]
        for item in request.get("tools", [])
        if item.get("type") == "function"
    }


def _system_prompt(request: dict[str, Any]) -> str:
    return str(
        next(item for item in request["messages"] if item["role"] == "system")[
            "content"
        ]
    )


@pytest.mark.asyncio
async def test_same_title_recreation_gets_fresh_storage_and_runtime_identity(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing a deleted title must not reconnect the new subentry to old state."""
    MockUser(id=_USER_ID, name="Same Title Owner", is_owner=True).add_to_hass(hass)
    entry = _entry()
    await _setup_entry(hass, entry)

    old_subentry = next(item for item in entry.subentries.values() if item.title == _TITLE)
    old_id = old_subentry.subentry_id
    old_row = _conversation_row(hass, entry.entry_id, old_id)
    old_agent = conversation.async_get_agent(hass, old_row.entity_id)
    assert old_agent is not None
    assert old_agent.subentry.subentry_id == old_id
    assert old_agent._memory is not None
    assert old_agent._temporary_memory is not None
    assert old_agent._archive is not None
    assert old_agent._function_groups_runtime is not None

    old_memory = old_agent._memory
    old_temporary = old_agent._temporary_memory
    old_archive = old_agent._archive
    old_usage = old_agent._usage
    old_runtime = old_agent._function_groups_runtime

    # Seed every per-subentry state family called out by this regression. These are
    # genuine production managers/stores created by the loaded HA conversation entity.
    await old_memory.async_add(
        _USER_ID,
        _MEMORY_MARKER,
        "acceptance",
        "explicit",
    )
    owner_scope = f"user:{_USER_ID}"
    await old_temporary.async_add(
        owner_scope,
        _TEMPORARY_MARKER,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=owner_scope,
    )

    scope = user_scope(_USER_ID, source="same_title_recreation_test")
    old_archive_session = await old_archive.async_begin_session(
        "deleted-generation-session",
        scope,
        "deleted-generation-ha-conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert old_archive_session is not None
    await old_archive.async_record_turn(
        old_archive_session.session_id,
        run_id="deleted-generation-run",
        user_text=_ARCHIVE_MARKER,
        assistant_text="Old archive response marker.",
        successful=True,
    )

    async with old_usage.async_run(
        home_assistant_conversation_id="deleted-generation-usage"
    ):
        await old_usage.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=7, output_tokens=5, total_tokens=12),
            provider="openai",
            model="gpt-5.6",
            api_mode=API_MODE_CHAT_COMPLETIONS,
            request_stage="initial",
        )

    old_group_session = old_runtime.begin("deleted-generation-function-session", 30)
    old_group_session.loaded_group_ids.add(_GROUP_ID)

    assert [
        record.content
        for record in await old_memory.async_search(_USER_ID, "amber cipher")
    ] == [_MEMORY_MARKER]
    assert [
        record.content
        for record in await old_temporary.async_active(
            owner_scope, owner_scope_id=owner_scope
        )
    ] == [_TEMPORARY_MARKER]
    assert (await old_archive.async_search(scope.scope_id, "violet-orbit"))["results"]
    assert old_usage.totals.conversation_count == 1
    assert old_usage.totals.api_request_count == 1
    assert len(old_usage.requests) == 1
    assert len(old_usage.runs) == 1
    assert old_runtime.stats()["active_function_group_sessions"] == 1

    assert hass.config_entries.async_remove_subentry(entry, old_id)
    await hass.async_block_till_done()

    assert old_id not in entry.subentries
    assert not any(
        row.config_subentry_id == old_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    )
    # Function-group state is explicitly lifecycle-owned rather than merely hidden by
    # the entity registry. The old runtime key must be removed on entity teardown.
    assert get_function_group_runtime(hass, entry.entry_id, old_id) is None

    recreated = ConfigSubentry(
        data=MappingProxyType(_options()),
        subentry_type="conversation",
        title=_TITLE,
        unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(entry, recreated)
    await hass.async_block_till_done()

    assert recreated.title == old_subentry.title == _TITLE
    assert recreated.subentry_id != old_id
    assert recreated.subentry_id in entry.subentries

    new_row = _conversation_row(hass, entry.entry_id, recreated.subentry_id)
    new_agent = conversation.async_get_agent(hass, new_row.entity_id)
    assert new_agent is not None
    assert new_agent is not old_agent
    assert new_agent.subentry.subentry_id == recreated.subentry_id
    assert new_row.config_subentry_id == recreated.subentry_id

    assert new_agent._memory is not None
    assert new_agent._temporary_memory is not None
    assert new_agent._archive is not None
    assert new_agent._function_groups_runtime is not None
    assert new_agent._memory is not old_memory
    assert new_agent._temporary_memory is not old_temporary
    assert new_agent._archive is not old_archive
    assert new_agent._usage is not old_usage
    assert new_agent._function_groups_runtime is not old_runtime

    # The replacement has the same user-facing title and identical feature config,
    # but every persisted/runtime state family must start as a new generation.
    assert await new_agent._memory.async_search(_USER_ID, "amber cipher") == []
    assert (
        await new_agent._temporary_memory.async_active(
            owner_scope, owner_scope_id=owner_scope
        )
        == []
    )
    assert (await new_agent._archive.async_search(scope.scope_id, "violet-orbit"))[
        "results"
    ] == []
    assert new_agent._archive.stats()["session_count"] == 0
    assert new_agent._archive.stats()["turn_count"] == 0
    assert new_agent._usage.totals.conversation_count == 0
    assert new_agent._usage.totals.api_request_count == 0
    assert new_agent._usage.requests == []
    assert new_agent._usage.runs == []
    assert new_agent._function_groups_runtime.stats()[
        "active_function_group_sessions"
    ] == 0

    # The old generation remains intact through our retained references. This guards
    # against recreation "cleaning" state by mutating the old manager instead of
    # creating a genuinely independent subentry generation.
    assert [
        record.content
        for record in await old_memory.async_search(_USER_ID, "amber cipher")
    ] == [_MEMORY_MARKER]
    assert [
        record.content
        for record in await old_temporary.async_active(
            owner_scope, owner_scope_id=owner_scope
        )
    ] == [_TEMPORARY_MARKER]
    assert (await old_archive.async_search(scope.scope_id, "violet-orbit"))["results"]
    assert old_usage.totals.api_request_count == 1

    # Finally cross the public Assist/provider seam. Old Memory/Temporary Memory must
    # not appear in the replacement prompt, and the old generation's loaded on-demand
    # Function Group must not make its member tool visible in the replacement request.
    sent = _provider(monkeypatch, new_agent, ["Fresh same-title generation confirmed."])
    result = await conversation.async_converse(
        hass=hass,
        text="Confirm this recreated conversation starts clean.",
        conversation_id=None,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=new_row.entity_id,
    )
    assert _speech(result) == "Fresh same-title generation confirmed."
    assert len(sent) == 1
    serialized = json.dumps(sent[0], ensure_ascii=False, sort_keys=True)
    assert _MEMORY_MARKER not in serialized
    assert _TEMPORARY_MARKER not in serialized
    assert _ARCHIVE_MARKER not in serialized
    assert _GROUP_TOOL not in _tool_names(sent[0])
    assert _MEMORY_MARKER not in _system_prompt(sent[0])
    assert _TEMPORARY_MARKER not in _system_prompt(sent[0])
