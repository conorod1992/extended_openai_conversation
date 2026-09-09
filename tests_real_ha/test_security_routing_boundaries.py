"""Real Home Assistant acceptance tests for security and request-routing boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    get_active_ha_context,
)


def _entry(title: str = "Security Boundaries") -> MockConfigEntry:
    """Build one local-only conversation entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-security-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": f"{title} Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up through Home Assistant so auth caching and runtime wrappers are real."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _restricted_user(hass: HomeAssistant, user_id: str, entity_id: str) -> MockUser:
    """Create a real HA auth-model user whose entity policy names one entity only."""
    user = MockUser(id=user_id, name=user_id.title())
    user.mock_policy({"entities": {"entity_ids": {entity_id: True}}})
    user.add_to_hass(hass)
    return user


async def _converse(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    user_id: str,
    conversation_id: str,
    text: str = "test security boundary",
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public Conversation API with an HA identity."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=entry.entry_id,
    )


def _install_local_provider_seam(
    monkeypatch: pytest.MonkeyPatch,
    agent: ExtendedOpenAIAgentEntity,
    handler: Callable[..., Any],
) -> None:
    """Replace only provider transport while retaining the full request pipeline."""
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", handler)


@pytest.mark.asyncio
async def test_public_conversation_keeps_concurrent_user_permissions_isolated(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping authenticated requests must never share permission context."""
    hass.states.async_set("light.alice_only", "off", {"friendly_name": "Alice Light"})
    hass.states.async_set("light.bob_only", "off", {"friendly_name": "Bob Light"})
    async_expose_entity(hass, conversation.DOMAIN, "light.alice_only", True)
    async_expose_entity(hass, conversation.DOMAIN, "light.bob_only", True)

    alice = _restricted_user(hass, "alice", "light.alice_only")
    bob = _restricted_user(hass, "bob", "light.bob_only")
    entry = _entry()
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    arrived = 0
    arrived_lock = asyncio.Lock()
    both_arrived = asyncio.Event()
    observed: dict[str, tuple[str | None, set[str]]] = {}

    async def local_seam(user_input: Any, chat_log: Any, _options: Any = None) -> Any:
        nonlocal arrived
        async with arrived_lock:
            arrived += 1
            if arrived == 2:
                both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)

        active = get_active_ha_context()
        user_id = user_input.context.user_id
        observed[user_id] = (
            active.user_id if active is not None else None,
            {item["entity_id"] for item in agent._get_exposed_entities()},
        )
        return agent._local_rule_result(
            user_input, chat_log, "done", successful=True
        )

    _install_local_provider_seam(monkeypatch, agent, local_seam)

    await asyncio.gather(
        _converse(
            hass,
            entry,
            user_id=alice.id,
            conversation_id="security-alice",
        ),
        _converse(
            hass,
            entry,
            user_id=bob.id,
            conversation_id="security-bob",
        ),
    )

    assert observed == {
        alice.id: (alice.id, {"light.alice_only"}),
        bob.id: (bob.id, {"light.bob_only"}),
    }
    assert get_active_ha_context() is None


@pytest.mark.asyncio
async def test_public_conversation_enforces_control_permission_and_context(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_read_only_user: MockUser,
    hass_admin_user: MockUser,
) -> None:
    """Model-driven HA actions deny read-only callers and retain admin context."""
    hass.states.async_set("light.restricted", "off")
    calls: list[ServiceCall] = []

    async def turn_on(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_on", turn_on)

    entry = _entry("Control Boundary")
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    outcomes: dict[str, str] = {}

    async def local_seam(user_input: Any, chat_log: Any, _options: Any = None) -> Any:
        user_id = user_input.context.user_id
        try:
            await async_call_ha_action(
                hass,
                "light",
                "turn_on",
                target={"entity_id": ["light.restricted"]},
                blocking=True,
            )
        except HomeAssistantError as err:
            outcomes[user_id] = str(err)
        else:
            outcomes[user_id] = "allowed"
        return agent._local_rule_result(
            user_input, chat_log, "done", successful=True
        )

    _install_local_provider_seam(monkeypatch, agent, local_seam)

    await _converse(
        hass,
        entry,
        user_id=hass_read_only_user.id,
        conversation_id="security-read-only",
    )
    await _converse(
        hass,
        entry,
        user_id=hass_admin_user.id,
        conversation_id="security-admin",
    )

    assert "does not have permission" in outcomes[hass_read_only_user.id]
    assert outcomes[hass_admin_user.id] == "allowed"
    assert len(calls) == 1
    assert calls[0].context.user_id == hass_admin_user.id
    assert get_active_ha_context() is None


@pytest.mark.asyncio
async def test_public_conversation_missing_and_inactive_users_fail_closed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale authenticated identities must not inherit anonymous model visibility."""
    hass.states.async_set("light.private", "off")
    async_expose_entity(hass, conversation.DOMAIN, "light.private", True)

    inactive = MockUser(id="inactive", is_active=False, name="Inactive")
    inactive.mock_policy({"entities": True})
    inactive.add_to_hass(hass)

    entry = _entry("Fail Closed")
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    observed: dict[str, list[str]] = {}

    async def local_seam(user_input: Any, chat_log: Any, _options: Any = None) -> Any:
        observed[user_input.context.user_id] = [
            item["entity_id"] for item in agent._get_exposed_entities()
        ]
        return agent._local_rule_result(
            user_input, chat_log, "done", successful=True
        )

    _install_local_provider_seam(monkeypatch, agent, local_seam)

    await _converse(
        hass,
        entry,
        user_id=inactive.id,
        conversation_id="security-inactive",
    )
    await _converse(
        hass,
        entry,
        user_id="missing-user",
        conversation_id="security-missing",
    )

    assert observed == {inactive.id: [], "missing-user": []}
    assert get_active_ha_context() is None
