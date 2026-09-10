"""Cross-boundary acceptance coverage for persistent Memory in a conversation."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    CONF_PROMPT,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_MEMORY_SESSION,
    _ACTIVE_SCOPE,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import llm


class FakeStorage:
    """Detached durable storage for the real PersistentMemory manager."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)

    async def async_load(self) -> dict[str, Any] | None:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


def _request(text: str, context: Context) -> tuple[Any, Any]:
    llm_context = SimpleNamespace(context=context, device_id="kitchen")
    user_input = SimpleNamespace(
        text=text,
        language="en",
        conversation_id="conversation-memory-acceptance",
        context=context,
        device_id="kitchen",
        satellite_id=None,
    )
    user_input.as_llm_context = lambda _domain: llm_context
    return user_input, llm_context


def _chat_log(text: str) -> Any:
    return SimpleNamespace(
        content=[
            conversation.SystemContent(content="placeholder"),
            conversation.UserContent(content=text),
        ],
        conversation_id="conversation-memory-acceptance",
        continue_conversation=False,
    )


async def test_memory_store_bundle_prompt_and_tool_update_form_one_runtime_contract(
    hass,
) -> None:
    """Stored facts reach the provider seam and bundle reuse resolves updated state."""
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    created = await memory.async_upsert(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
    )
    memory_id = created["memory"]["memory_id"]

    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry", data={})
    entity.subentry = SimpleNamespace(
        subentry_id="agent",
        data={
            CONF_MEMORY_ENABLED: True,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 5,
            CONF_PROMPT: "You are the Memory acceptance test assistant.",
        },
    )
    entity._attr_entity_id = "conversation.memory_acceptance"
    entity._memory = memory
    entity._continuity = ConversationContinuity("agent")
    entity._temporary_memory = None
    entity._knowledge = None
    entity._usage = None
    entity._archive = None
    entity._resolve_live_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    entity._get_exposed_entities = MagicMock(return_value=[])
    entity._get_function_tools = MagicMock(return_value=[])
    entity._get_enabled_skills = MagicMock(return_value=[])
    entity._fire_conversation_finished = MagicMock()

    provider_prompts: list[str] = []

    async def provider_seam(chat_log, **_kwargs) -> bool:
        provider_prompts.append(chat_log.content[0].content)
        chat_log.content.append(
            conversation.AssistantContent(
                agent_id=entity.entity_id,
                content="Provider reply",
            )
        )
        return False

    entity._async_handle_chat_log = AsyncMock(side_effect=provider_seam)

    context = Context(user_id="alice")
    first_input, llm_context = _request("What breed is Oscar?", context)
    scope_token = _ACTIVE_SCOPE.set(
        user_scope("alice", source="test", device_id="kitchen")
    )
    session_token = _ACTIVE_MEMORY_SESSION.set(("memory-runtime-acceptance", 30))
    try:
        await entity._async_handle_message(first_input, _chat_log(first_input.text))

        assert len(provider_prompts) == 1
        assert "Oscar is a Cavachon." in provider_prompts[0]
        bundle = await entity._continuity.async_get_memory_bundle(
            "memory-runtime-acceptance", 30
        )
        assert bundle is not None
        assert list(bundle) == [("alice", memory_id)]

        tool_result = await entity._execute_function_tool(
            {
                "spec": {"name": "memory_upsert"},
                "function": {"type": "memory", "operation": "upsert"},
            },
            llm.ToolInput(
                id="memory-tool-call",
                tool_name="memory_upsert",
                tool_args={
                    "content": "Oscar is a Cavapoo.",
                    "category": "pets",
                    "source": "explicit",
                    "subject": "Oscar",
                    "key": "pet.oscar.breed",
                },
                external=True,
            ),
            llm_context,
            [],
        )
        tool_payload = json.loads(tool_result.tool_result["result"])
        assert tool_payload["status"] == "updated"
        assert tool_payload["memory"]["memory_id"] == memory_id

        second_input, _ = _request("Remind me about Oscar's breed.", context)
        await entity._async_handle_message(second_input, _chat_log(second_input.text))

        assert len(provider_prompts) == 2
        assert "Oscar is a Cavapoo." in provider_prompts[1]
        assert "Oscar is a Cavachon." not in provider_prompts[1]
        assert list(
            await entity._continuity.async_get_memory_bundle(
                "memory-runtime-acceptance", 30
            )
            or []
        ) == [("alice", memory_id)]
    finally:
        _ACTIVE_MEMORY_SESSION.reset(session_token)
        _ACTIVE_SCOPE.reset(scope_token)
