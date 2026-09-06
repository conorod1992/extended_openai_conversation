"""Regression tests for Voice Identity device-registry resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import conversation as agent_module
from custom_components.extended_openai_conversation_responses.const import (
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEVICE_MAPPING,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    user_scope,
)
from homeassistant.components import conversation
from homeassistant.core import Context


def _conversation_input(
    *,
    device_id: str | None,
    satellite_id: str | None,
    user_id: str | None = None,
) -> conversation.ConversationInput:
    return conversation.ConversationInput(
        text="hello",
        context=Context(user_id=user_id),
        conversation_id=None,
        device_id=device_id,
        satellite_id=satellite_id,
        language="en",
        agent_id="conversation.agent",
    )


def _processing_entity(options: dict) -> ExtendedOpenAIAgentEntity:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = MagicMock()
    entity.subentry = SimpleNamespace(data=options)
    entity._resolve_live_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    entity._continuity = SimpleNamespace(
        async_resolve=AsyncMock(
            return_value=SimpleNamespace(
                key="continuity-key",
                claim_token="claim-token",
                conversation_id="conversation-id",
                history=[],
            )
        ),
        async_release=AsyncMock(),
    )
    entity._async_process_claimed = AsyncMock(return_value=object())
    return entity


def _claimed_arguments(entity: ExtendedOpenAIAgentEntity):
    args = entity._async_process_claimed.await_args.args
    return args[1], args[3], args[4]


async def test_both_ids_use_device_registry_id_for_direct_processing(monkeypatch) -> None:
    """A satellite entity ID never overrides the registry device supplied by HA."""
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"registry-device": "user:alice"},
    }
    entity = _processing_entity(options)
    monkeypatch.setattr(
        agent_module.er,
        "async_get",
        MagicMock(side_effect=AssertionError("registry fallback must not be used")),
    )
    user_input = _conversation_input(
        device_id="registry-device",
        satellite_id="assist_satellite.kitchen",
    )

    _result, metadata = await entity.async_process_direct(user_input)

    llm_context, scope, source_device_id = _claimed_arguments(entity)
    assert metadata == {"handled_locally": False}
    assert scope.scope_id == "user:alice"
    assert scope.device_id == "registry-device"
    assert source_device_id == "registry-device"
    assert llm_context.device_id == "registry-device"
    assert entity._continuity.async_resolve.await_args.args[2] == "registry-device"
    assert user_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.parametrize(
    ("options", "user_id", "expected_scope", "expected_type"),
    [
        (
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"registry-device": "shared"},
            },
            None,
            SHARED_HOUSEHOLD_SCOPE_ID,
            "shared",
        ),
        (
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {},
                CONF_VOICE_UNMAPPED_POLICY: "unretained",
            },
            None,
            "unretained",
            "unretained",
        ),
        (
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"registry-device": "user:bob"},
            },
            "authenticated-owner",
            "user:authenticated-owner",
            "user",
        ),
    ],
)
async def test_registry_device_drives_shared_unretained_and_authenticated_scope(
    options, user_id, expected_scope, expected_type
) -> None:
    """All identity policies consume the same registry-device source."""
    entity = _processing_entity(options)
    user_input = _conversation_input(
        device_id="registry-device",
        satellite_id="assist_satellite.kitchen",
        user_id=user_id,
    )

    await entity._async_process(user_input)

    llm_context, scope, source_device_id = _claimed_arguments(entity)
    assert scope.scope_id == expected_scope
    assert scope.scope_type == expected_type
    assert scope.device_id == "registry-device"
    assert source_device_id == "registry-device"
    assert llm_context.device_id == "registry-device"


async def test_satellite_only_request_resolves_its_registry_device(monkeypatch) -> None:
    """Supported satellite-only callers resolve the entity before identity lookup."""
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"registry-device": "user:alice"},
    }
    entity = _processing_entity(options)
    registry = SimpleNamespace(
        async_get=MagicMock(
            return_value=SimpleNamespace(device_id="registry-device")
        )
    )
    monkeypatch.setattr(agent_module.er, "async_get", lambda _hass: registry)
    user_input = _conversation_input(
        device_id=None,
        satellite_id="assist_satellite.kitchen",
    )

    await entity._async_process(user_input)

    llm_context, scope, source_device_id = _claimed_arguments(entity)
    registry.async_get.assert_called_once_with("assist_satellite.kitchen")
    assert scope.scope_id == "user:alice"
    assert scope.device_id == "registry-device"
    assert source_device_id == "registry-device"
    assert llm_context.device_id == "registry-device"
    assert user_input.device_id is None
    assert user_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.parametrize("satellite_id", [None, "assist_satellite.unknown"])
async def test_missing_device_id_never_reinterprets_satellite_entity_id(
    monkeypatch, satellite_id
) -> None:
    """No registry device means unretained scope instead of an entity-ID device key."""
    entity = _processing_entity(
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {
                "assist_satellite.unknown": "user:wrong-owner"
            },
            CONF_VOICE_UNMAPPED_POLICY: "unretained",
        }
    )
    registry = SimpleNamespace(async_get=MagicMock(return_value=None))
    monkeypatch.setattr(agent_module.er, "async_get", lambda _hass: registry)
    user_input = _conversation_input(device_id=None, satellite_id=satellite_id)

    await entity._async_process(user_input)

    llm_context, scope, source_device_id = _claimed_arguments(entity)
    assert scope.scope_type == "unretained"
    assert scope.device_id is None
    assert source_device_id is None
    assert llm_context.device_id is None
    assert entity._continuity.async_resolve.await_args.args[2] is None


def _message_entity() -> ExtendedOpenAIAgentEntity:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=MagicMock()))
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    entity._attr_entity_id = "conversation.agent"
    entity._usage = None
    entity._get_exposed_entities = MagicMock(return_value=[])
    entity._get_function_tools = MagicMock(return_value=[])
    entity._async_retrieve_memories = AsyncMock(return_value=[])
    entity._async_retrieve_temporary_memories = AsyncMock(return_value=[])
    entity._build_system_prompt = MagicMock(return_value="system")
    return entity


async def test_prompt_memory_and_provider_share_the_resolved_request_device() -> None:
    """Prompt construction cannot drift from the scope selected at request entry."""
    entity = _message_entity()
    user_input = _conversation_input(
        device_id=None,
        satellite_id="assist_satellite.kitchen",
    )
    user_input.conversation_id = "conversation-id"
    chat_log = SimpleNamespace(
        content=[conversation.UserContent(content="hello")],
        conversation_id="conversation-id",
        continue_conversation=False,
    )
    captured = {}

    async def succeed(log, **kwargs):
        captured["provider_context"] = kwargs["llm_context"]
        log.content.append(
            conversation.AssistantContent(agent_id=entity.entity_id, content="hi")
        )
        return None

    entity._async_handle_chat_log = AsyncMock(side_effect=succeed)
    scope_token = agent_module._ACTIVE_SCOPE.set(
        user_scope("alice", source="test", device_id="registry-device")
    )
    try:
        await entity._async_handle_message(user_input, chat_log)
    finally:
        agent_module._ACTIVE_SCOPE.reset(scope_token)

    prompt_context = entity._build_system_prompt.call_args.args[1]
    memory_context = entity._async_retrieve_memories.call_args.args[0]
    provider_context = captured["provider_context"]
    assert prompt_context is memory_context is provider_context
    assert prompt_context.device_id == "registry-device"
