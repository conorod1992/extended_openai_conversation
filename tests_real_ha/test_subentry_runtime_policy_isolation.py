"""Real-HA acceptance for conversation-subentry runtime policy isolation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_POLICY_VERSION,
    CONF_MEMORY_MODE,
    CONF_REASONING_EFFORT,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPORARY_MEMORY,
    CONFIG_ENTRY_VERSION,
    CONVERSATION_CONTINUITY_USER,
    DOMAIN,
    GUEST_POLICY_VERSION,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
    TEMPORARY_MEMORY_BALANCED,
    TEMPORARY_MEMORY_OFF,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _setup_entry, _subentry
from tests_real_ha.test_cross_feature_acceptance import _provider, _speech

_USER_ID = "subentry-policy-owner"
_A_TITLE = "Policy A"
_B_TITLE = "Policy B"
_A_TOOL = "subentry_a_only"
_B_TOOL = "subentry_b_guest_blocked"
_DURABLE_MARKER = "Subentry A durable policy marker is amber-cipher."
_TEMPORARY_MARKER = "Subentry A temporary policy marker is teal-comet."


def _template_tool(name: str, result: str) -> dict[str, Any]:
    """Return one deterministic configured Function Tool."""
    return {
        "spec": {
            "name": name,
            "description": f"Return the {name} acceptance marker.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": result},
        "enabled": True,
    }


def _entry() -> MockConfigEntry:
    """Build one parent entry with deliberately conflicting conversation policies."""
    common = {
        CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        CONF_CHAT_MODEL: "gpt-5.6",
        CONF_REASONING_EFFORT: "none",
        CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
        CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
    }
    policy_a = {
        **common,
        CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        CONF_GUEST_MODE_ENABLED: False,
        CONF_GUEST_FUNCTION_POLICY: "on",
        CONF_FUNCTION_TOOLS: [_template_tool(_A_TOOL, "subentry-a")],
    }
    policy_b = {
        **common,
        CONF_MEMORY_MODE: MEMORY_MODE_OFF,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_OFF,
        CONF_GUEST_MODE_ENABLED: True,
        CONF_GUEST_FUNCTION_POLICY: "off",
        CONF_FUNCTION_TOOLS: [_template_tool(_B_TOOL, "subentry-b")],
    }
    return MockConfigEntry(
        domain=DOMAIN,
        title="Subentry Runtime Policy Isolation",
        data={
            CONF_API_KEY: "sk-subentry-policy-isolation",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", _A_TITLE, policy_a),
            _subentry("conversation", _B_TITLE, policy_b),
        ],
    )


def _agent_by_title(
    hass: HomeAssistant, entry: MockConfigEntry, title: str
) -> tuple[Any, Any]:
    """Resolve a conversation subentry and its real HA conversation entity."""
    subentry = next(item for item in entry.subentries.values() if item.title == title)
    registry_row = next(
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.config_subentry_id == subentry.subentry_id
        and row.domain == conversation.DOMAIN
    )
    agent = conversation.async_get_agent(hass, registry_row.entity_id)
    assert agent is not None
    return subentry, agent


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public Conversation API as the same owner."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=agent.entity_id,
    )


def _tool_names(request: dict[str, Any]) -> set[str]:
    """Return Chat Completions function names advertised on one request."""
    return {
        item["function"]["name"]
        for item in request.get("tools", [])
        if item.get("type") == "function"
    }


def _system_prompt(request: dict[str, Any]) -> str:
    """Return the serialized system prompt."""
    return str(
        next(item for item in request["messages"] if item["role"] == "system")[
            "content"
        ]
    )


def _assert_policy_a_request(request: dict[str, Any]) -> None:
    """Assert the provider sees only subentry A's permissive runtime policy."""
    names = _tool_names(request)
    prompt = _system_prompt(request)

    assert _A_TOOL in names
    assert _B_TOOL not in names
    assert "memory_search" in names
    assert "memory_upsert" in names
    assert "temporary_memory_add" in names
    assert "## Persistent memory" in prompt
    assert "## Guest Mode" not in prompt
    assert _DURABLE_MARKER in prompt
    assert _TEMPORARY_MARKER in prompt


def _assert_policy_b_request(request: dict[str, Any]) -> None:
    """Assert the provider sees only subentry B's restrictive runtime policy."""
    names = _tool_names(request)
    prompt = _system_prompt(request)

    assert _A_TOOL not in names
    # B really has this tool configured, but its active Guest policy denies configured
    # Function Tools. Seeing it here would prove that A/B runtime policy leaked.
    assert _B_TOOL not in names
    assert "memory_search" not in names
    assert "memory_upsert" not in names
    assert "temporary_memory_add" not in names
    assert "## Persistent memory" not in prompt
    assert "## Guest Mode" in prompt
    assert _DURABLE_MARKER not in prompt
    assert _TEMPORARY_MARKER not in prompt


async def test_two_conversation_subentries_keep_runtime_policies_isolated(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Alternating sibling requests must never share memory, Guest, or tools."""
    MockUser(id=_USER_ID, name="Subentry Policy Owner", is_owner=True).add_to_hass(hass)
    entry = _entry()
    await _setup_entry(hass, entry)

    subentry_a, agent_a = _agent_by_title(hass, entry, _A_TITLE)
    subentry_b, agent_b = _agent_by_title(hass, entry, _B_TITLE)

    assert agent_a.subentry.subentry_id == subentry_a.subentry_id
    assert agent_b.subentry.subentry_id == subentry_b.subentry_id
    assert agent_a._memory is not None
    assert agent_b._memory is None
    assert agent_a._temporary_memory is not None
    assert agent_b._temporary_memory is None
    assert agent_a._guest_mode is not agent_b._guest_mode
    assert not agent_a._guest_mode.is_active()
    assert not agent_b._guest_mode.is_active()

    configured_a = {
        tool["spec"]["name"] for tool in agent_a._get_configured_function_tools()
    }
    configured_b = {
        tool["spec"]["name"] for tool in agent_b._get_configured_function_tools()
    }
    assert configured_a == {_A_TOOL}
    assert configured_b == {_B_TOOL}

    await agent_a._memory.async_add(
        _USER_ID,
        _DURABLE_MARKER,
        "acceptance",
        "explicit",
    )
    owner_scope = f"user:{_USER_ID}"
    await agent_a._temporary_memory.async_add(
        owner_scope,
        _TEMPORARY_MARKER,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=owner_scope,
    )

    # Activate Guest Mode for B only. The sibling manager must remain untouched.
    await agent_b._guest_mode.async_update_trusted(indefinite=True)
    assert not agent_a._guest_mode.is_active()
    assert agent_b._guest_mode.is_active()

    # Alternate between the siblings twice. Reinstalling the provider seam before
    # each turn is intentional: parent-entry clients may be shared, while the tested
    # runtime policy and conversation entity remain per subentry.
    sent_a1 = _provider(monkeypatch, agent_a, ["A1 policy remained isolated."])
    result_a1 = await _say(
        hass, agent_a, "What is my subentry A durable policy marker?"
    )
    assert _speech(result_a1) == "A1 policy remained isolated."
    assert len(sent_a1) == 1
    _assert_policy_a_request(sent_a1[0])

    sent_b1 = _provider(monkeypatch, agent_b, ["B1 policy remained isolated."])
    result_b1 = await _say(
        hass, agent_b, "What is my subentry A durable policy marker?"
    )
    assert _speech(result_b1) == "B1 policy remained isolated."
    assert len(sent_b1) == 1
    _assert_policy_b_request(sent_b1[0])

    sent_a2 = _provider(monkeypatch, agent_a, ["A2 policy remained isolated."])
    result_a2 = await _say(
        hass,
        agent_a,
        "Check my subentry A policy marker again.",
        conversation_id=result_a1.conversation_id,
    )
    assert _speech(result_a2) == "A2 policy remained isolated."
    assert len(sent_a2) == 1
    _assert_policy_a_request(sent_a2[0])

    sent_b2 = _provider(monkeypatch, agent_b, ["B2 policy remained isolated."])
    result_b2 = await _say(
        hass,
        agent_b,
        "Check whether any private subentry policy leaked.",
        conversation_id=result_b1.conversation_id,
    )
    assert _speech(result_b2) == "B2 policy remained isolated."
    assert len(sent_b2) == 1
    _assert_policy_b_request(sent_b2[0])

    # The restricted sibling must not mutate or replace A's private stores.
    durable = await agent_a._memory.async_search(
        _USER_ID, "subentry durable policy marker"
    )
    assert [record.content for record in durable] == [_DURABLE_MARKER]
    temporary = await agent_a._temporary_memory.async_active(
        owner_scope, owner_scope_id=owner_scope
    )
    assert [record.content for record in temporary] == [_TEMPORARY_MARKER]
