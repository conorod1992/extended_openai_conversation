"""Nightly safety for caller-supplied conversation-ID collisions."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_same_conversation_id_cannot_cross_user_or_agent_boundaries(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A caller-selected HA conversation ID is owned by one agent/scope boundary."""
    users = [
        MockUser(id="collision-user-a", name="Collision A"),
        MockUser(id="collision-user-b", name="Collision B"),
    ]
    for user in users:
        user.add_to_hass(hass)

    entries = [
        _make_entry("Collision agent A", include_ai_task=False),
        _make_entry("Collision agent B", include_ai_task=False),
    ]
    for entry in entries:
        await _setup_entry(hass, entry)

    agents = [conversation.async_get_agent(hass, entry.entry_id) for entry in entries]
    assert all(isinstance(agent, ExtendedOpenAIAgentEntity) for agent in agents)

    private_markers = {
        (0, users[0].id): "PRIVATE_COLLISION_AGENT_A_USER_A",
        (0, users[1].id): "PRIVATE_COLLISION_AGENT_A_USER_B",
        (1, users[0].id): "PRIVATE_COLLISION_AGENT_B_USER_A",
    }
    observed: list[tuple[int, str, str]] = []

    def install_model(agent_index: int) -> None:
        agent = agents[agent_index]
        assert isinstance(agent, ExtendedOpenAIAgentEntity)

        async def model(log: conversation.ChatLog, **kwargs) -> None:
            del kwargs
            text = "\n".join(
                item.content
                for item in log.content
                if isinstance(getattr(item, "content", None), str)
            )
            current = next(
                marker for marker in private_markers.values() if marker in text
            )
            observed.append((agent_index, current, text))
            assert all(
                marker == current or marker not in text
                for marker in private_markers.values()
            ), text
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=agent.entity_id,
                    content=f"safe:{current}",
                )
            )

        monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    install_model(0)
    install_model(1)

    collision_id = "caller-selected-cross-scope-collision"
    results = []
    for agent_index, user in ((0, users[0]), (0, users[1]), (1, users[0])):
        marker = private_markers[(agent_index, user.id)]
        result = await conversation.async_converse(
            hass=hass,
            text=f"Keep this turn private: {marker}",
            conversation_id=collision_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=entries[agent_index].entry_id,
        )
        assert result.response.error_code is None
        assert result.response.as_dict()["speech"]["plain"]["speech"] == f"safe:{marker}"
        assert result.conversation_id
        results.append(result.conversation_id)

    assert len(set(results)) == 3
    assert results[0] == collision_id
    assert results[1] != collision_id
    assert results[2] != collision_id
    assert len(observed) == 3
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist",
        conversation_id_collision_probes=3,
        users=2,
        agents=2,
    )
