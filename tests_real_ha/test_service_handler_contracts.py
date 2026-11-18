"""Focused acceptance coverage for registered service handler contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import services
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_GUEST_MODE_DISABLE,
    SERVICE_GUEST_MODE_UPDATE,
    SERVICE_PROCESS,
    SERVICE_QUERY_IMAGE,
)
from tests_real_ha.test_service_registry_acceptance import (
    _conversation_entity_id,
    _conversation_subentry,
    _entry,
    _response_service_call,
    _setup_entry,
)


@pytest.mark.asyncio
async def test_process_service_success_preserves_direct_request_contract(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process must resolve the registered agent and return its public result shape."""
    entry = _entry()
    await _setup_entry(hass, entry)
    entity_id = _conversation_entity_id(hass, entry)

    result = SimpleNamespace(
        response=SimpleNamespace(speech={"plain": {"speech": "Kitchen light is on"}}),
        conversation_id="conversation-next",
    )
    agent = SimpleNamespace(
        entity_id=entity_id,
        async_process_direct=AsyncMock(
            return_value=(
                result,
                {
                    "handled_locally": True,
                    "matched_rule": "light-state",
                    "captured_values": {"room": "kitchen"},
                },
            )
        ),
    )
    monkeypatch.setattr(
        services.conversation,
        "async_get_agent",
        lambda _hass, requested: agent if requested == entity_id else None,
    )

    response = await _response_service_call(
        hass,
        SERVICE_PROCESS,
        {
            "text": "Is the kitchen light on?",
            "agent_id": entity_id,
            "conversation_id": "conversation-in",
            "device_id": "device-1",
            "satellite_id": "satellite-1",
            "language": "en-IE",
        },
    )

    assert response == {
        "response": "Kitchen light is on",
        "successful": True,
        "error_code": None,
        "error_message": None,
        "continue_conversation": False,
        "intent_response": {},
        "conversation_id": "conversation-next",
        "handled_locally": True,
        "matched_rule": "light-state",
        "captured_values": {"room": "kitchen"},
    }
    user_input = agent.async_process_direct.await_args.args[0]
    assert user_input.text == "Is the kitchen light on?"
    assert user_input.conversation_id == "conversation-in"
    assert user_input.device_id == "device-1"
    assert user_input.satellite_id == "satellite-1"
    assert user_input.language == "en-IE"
    assert user_input.agent_id == entity_id


@pytest.mark.asyncio
async def test_process_service_translates_unexpected_agent_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected direct-processing failures must cross the service boundary safely."""
    entry = _entry()
    await _setup_entry(hass, entry)
    entity_id = _conversation_entity_id(hass, entry)
    agent = SimpleNamespace(
        entity_id=entity_id,
        async_process_direct=AsyncMock(side_effect=RuntimeError("pipeline failed")),
    )
    monkeypatch.setattr(
        services.conversation,
        "async_get_agent",
        lambda _hass, requested: agent if requested == entity_id else None,
    )

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI could not process the request: pipeline failed",
    ):
        await _response_service_call(
            hass,
            SERVICE_PROCESS,
            {"text": "Hello", "agent_id": entity_id},
        )


@pytest.mark.asyncio
async def test_call_function_service_uses_default_arguments_and_unwraps_tool_result(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call Function must preserve schema defaults and the public result envelope."""
    entry = _entry()
    await _setup_entry(hass, entry)
    call_active = AsyncMock(return_value=SimpleNamespace(tool_result={"answer": 42}))
    monkeypatch.setattr(services, "async_call_active_function", call_active)

    response = await _response_service_call(
        hass,
        SERVICE_CALL_FUNCTION,
        {"function": "lookup_answer"},
    )

    assert response == {"result": {"answer": 42}}
    call_active.assert_awaited_once_with("lookup_answer", {})


@pytest.mark.asyncio
async def test_query_image_service_success_uses_loaded_runtime_client(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
) -> None:
    """Query Image must use the selected entry runtime and return provider data."""
    entry = _entry()
    await _setup_entry(hass, entry)
    provider_response = SimpleNamespace(
        id="response-1",
        status="completed",
        error=None,
        model_dump=MagicMock(
            return_value={"id": "response-1", "output_text": "A dog"}
        ),
    )
    create = AsyncMock(return_value=provider_response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    entry.runtime_data = client

    response = await _response_service_call(
        hass,
        SERVICE_QUERY_IMAGE,
        {
            "config_entry": entry.entry_id,
            "model": "gpt-4.1-mini",
            CONF_API_MODE: API_MODE_RESPONSES,
            "prompt": "What is shown?",
            "images": [{"url": "https://example.com/dog.png"}],
            "max_tokens": 123,
        },
        user_id=hass_admin_user.id,
    )

    assert response == {"id": "response-1", "output_text": "A dog"}
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-4.1-mini"
    assert kwargs["max_output_tokens"] == 123
    assert kwargs["store"] is False
    assert kwargs["input"][0]["content"] == [
        {"type": "input_text", "text": "What is shown?"},
        {
            "type": "input_image",
            "image_url": "https://example.com/dog.png",
            "detail": "auto",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_name", "helper_name", "field", "enabled"),
    [
        (
            services.SERVICE_ENABLE_FUNCTION_TOOLS,
            "async_set_function_tools_enabled",
            "functions",
            True,
        ),
        (
            services.SERVICE_DISABLE_FUNCTION_GROUPS,
            "async_set_function_groups_enabled",
            "function_groups",
            False,
        ),
    ],
)
async def test_function_state_services_dispatch_exact_requested_state(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
    service_name: str,
    helper_name: str,
    field: str,
    enabled: bool,
) -> None:
    """Function state services must dispatch exact target, names and state."""
    # tests/test_function_tool_services.py covers both states for both families.
    # Keep real dispatch for each distinct HA schema and registration for all names.
    entry = _entry()
    await _setup_entry(hass, entry)
    for registered_service in (
        services.SERVICE_ENABLE_FUNCTION_TOOLS,
        services.SERVICE_DISABLE_FUNCTION_TOOLS,
        services.SERVICE_ENABLE_FUNCTION_GROUPS,
        services.SERVICE_DISABLE_FUNCTION_GROUPS,
    ):
        assert hass.services.has_service(DOMAIN, registered_service)
    subentry = _conversation_subentry(entry)
    helper = AsyncMock()
    monkeypatch.setattr(services, helper_name, helper)

    await hass.services.async_call(
        DOMAIN,
        service_name,
        {
            "config_entry": entry.entry_id,
            "agent_id": subentry.subentry_id,
            field: ["first", "second"],
        },
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )

    helper.assert_awaited_once_with(
        hass,
        entry.entry_id,
        subentry.subentry_id,
        ["first", "second"],
        enabled,
    )


@pytest.mark.asyncio
async def test_function_state_service_unknown_target_does_not_mutate(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
) -> None:
    """Target resolution must fail before any Function Tool mutation occurs."""
    entry = _entry()
    await _setup_entry(hass, entry)

    with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
        await hass.services.async_call(
            DOMAIN,
            services.SERVICE_ENABLE_FUNCTION_TOOLS,
            {
                "config_entry": entry.entry_id,
                "agent_id": "missing-agent",
                "functions": ["anything"],
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


@pytest.mark.asyncio
async def test_guest_mode_services_dispatch_registered_actions(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
) -> None:
    """Guest Mode actions must cross the real HA registry and dispatch correctly."""
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    manager = SimpleNamespace(
        async_update_trusted=AsyncMock(return_value={"enabled": True}),
        async_disable_trusted=AsyncMock(return_value={"enabled": False}),
    )
    getter = AsyncMock(return_value=manager)
    monkeypatch.setattr(services, "async_get_guest_mode", getter)
    base = {"config_entry": entry.entry_id, "agent_id": subentry.subentry_id}

    updated = await _response_service_call(
        hass,
        SERVICE_GUEST_MODE_UPDATE,
        {**base, "indefinite": False},
        user_id=hass_admin_user.id,
    )
    assert updated == {"enabled": True}
    getter.assert_awaited_with(hass, entry.entry_id, subentry.subentry_id)
    manager.async_update_trusted.assert_awaited_once_with(
        active_from=None,
        active_until=None,
        indefinite=False,
    )

    disabled = await _response_service_call(
        hass,
        SERVICE_GUEST_MODE_DISABLE,
        base,
        user_id=hass_admin_user.id,
    )
    assert disabled == {"enabled": False}
    manager.async_disable_trusted.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_process_service_preserves_assist_failure_and_followup(hass, monkeypatch):
    from homeassistant.components.conversation import ConversationResult
    from homeassistant.helpers.intent import IntentResponse, IntentResponseErrorCode
    entry = _entry()
    await _setup_entry(hass, entry)
    entity_id = _conversation_entity_id(hass, entry)
    intent = IntentResponse(language="en")
    intent.async_set_error(IntentResponseErrorCode.UNKNOWN, "Provider request failed")
    result = ConversationResult(response=intent, conversation_id="failed-turn", continue_conversation=True)
    agent = SimpleNamespace(entity_id=entity_id, async_process_direct=AsyncMock(return_value=(result, {"handled_locally": False})))
    monkeypatch.setattr(services.conversation, "async_get_agent", lambda *_: agent)
    response = await _response_service_call(hass, SERVICE_PROCESS, {"text": "Hello", "agent_id": entity_id})
    assert response["successful"] is False
    assert response["error_code"] == "unknown"
    assert response["error_message"] == "Provider request failed"
    assert response["continue_conversation"] is True
    assert response["intent_response"] == intent.as_dict()
    await hass.async_block_till_done()

@pytest.mark.parametrize(
    ("base_url", "expected_endpoint", "expected_model", "native"),
    [
        (None, "https://compatible.example/v1", "gpt-4.1-mini", False),
        ("https://api.openai.com/v1", "https://api.openai.com/v1", "gpt-6-luna", True),
    ],
)
async def test_image_default_and_retirement_use_resolved_sdk_endpoint(
    hass, monkeypatch, base_url, expected_endpoint, expected_model, native
):
    """Implicit endpoints follow the SDK, and explicitly saved URLs take priority."""
    from datetime import date

    from homeassistant.components import conversation
    from homeassistant.helpers import issue_registry as ir

    from custom_components.extended_openai_conversation_responses import model_lifecycle
    from custom_components.extended_openai_conversation_responses.provider_errors import (
        ProviderStreamError,
    )
    from tests_real_ha.test_acceptance_lifecycle import _make_entry
    from tests_real_ha.test_provider_wire_e2e import (
        _install_wire,
        _raw_client,
        _response_object,
        _response_text_item,
    )

    lifecycle_snapshot = model_lifecycle.lifecycle_snapshot
    monkeypatch.setattr(
        model_lifecycle, "lifecycle_snapshot",
        lambda model, **_kwargs: lifecycle_snapshot(model, today=date(2027, 4, 2)),
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "https://compatible.example/v1")
    entry = _make_entry(
        include_ai_task=False, base_url=base_url, conversation_options={"chat_model": "gpt-5.1"}
    )
    subentry = _conversation_subentry(entry)
    error = ProviderStreamError("missing", code="model_not_found", status_code=404)
    assert model_lifecycle.record_retirement_failure(
        hass, entry_id=entry.entry_id, subentry_id=subentry.subentry_id,
        title=subentry.title, model="gpt-5.1", error=error,
        entry_data={"base_url": "https://api.openai.com/v1"},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert str(_raw_client(agent).base_url).rstrip("/") == expected_endpoint
    issue_id = model_lifecycle._issue_id(entry.entry_id, subentry.subentry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert (issue is not None) is native

    # Classification follows the existing SDK client even if the process env changes.
    monkeypatch.delenv("OPENAI_BASE_URL")
    wire = _install_wire(
        monkeypatch, agent, [(200, _response_object("resp-image", [_response_text_item("A dog")]))]
    )
    await _response_service_call(
        hass, SERVICE_QUERY_IMAGE,
        {"config_entry": entry.entry_id, CONF_API_MODE: API_MODE_RESPONSES,
         "prompt": "What is shown?", "images": [{"url": "https://image.example/dog.png"}]},
    )
    assert wire.requests[0]["body"]["model"] == expected_model
    assert bool(model_lifecycle.record_entity_retirement_failure(agent, error)) is native
    assert model_lifecycle.configured_lifecycle(
        subentry, entry_data=entry.data, client=entry.runtime_data,
    )["shutdown_reached"] is native
