"""Focused acceptance coverage for registered service handler contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import services, skills
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    SERVICE_DOWNLOAD_SKILL,
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
    call_active = AsyncMock(
        return_value=SimpleNamespace(tool_result={"answer": 42})
    )
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
        model_dump=MagicMock(return_value={"id": "response-1", "output_text": "A dog"}),
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


class _AsyncResponse:
    """Minimal aiohttp response context used at the external download boundary."""

    def __init__(self, status: int = 200) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_download_skill_service_success_publishes_staged_skill(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
    tmp_path: Path,
) -> None:
    """A valid skill download must stage, validate, publish and report exact files."""
    entry = _entry()
    await _setup_entry(hass, entry)

    manager = SimpleNamespace(
        user_skills_dir=tmp_path / "skills",
        staging_dir=tmp_path / "staging",
        async_publish_staged_skill=AsyncMock(),
    )
    monkeypatch.setattr(
        skills.SkillManager,
        "async_get_instance",
        AsyncMock(return_value=manager),
    )
    monkeypatch.setattr(
        services,
        "async_skill_source_ref",
        AsyncMock(return_value="v-test"),
    )
    session = SimpleNamespace(get=MagicMock(return_value=_AsyncResponse()))
    monkeypatch.setattr(services, "async_get_clientsession", lambda _hass: session)
    monkeypatch.setattr(
        services,
        "async_read_bounded_json",
        AsyncMock(
            return_value=[
                {
                    "name": "SKILL.md",
                    "type": "file",
                    "path": "skills/demo/SKILL.md",
                    "download_url": "https://example.com/SKILL.md",
                    "size": 6,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        services,
        "async_read_bounded_response",
        AsyncMock(return_value=b"# Demo"),
    )

    response = await _response_service_call(
        hass,
        SERVICE_DOWNLOAD_SKILL,
        {"skill_name": "demo", "source_ref": "v-test"},
        user_id=hass_admin_user.id,
    )

    assert response["skill_name"] == "demo"
    assert response["source_ref"] == "v-test"
    assert response["downloaded_files"] == ["skills/demo/SKILL.md"]
    assert response["target_directory"] == str((tmp_path / "skills" / "demo").resolve())
    manager.async_publish_staged_skill.assert_awaited_once()
    published_name, staging_dir = manager.async_publish_staged_skill.await_args.args
    assert published_name == "demo"
    assert (staging_dir / "SKILL.md").read_bytes() == b"# Demo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_name", "helper_name", "field", "enabled"),
    [
        (services.SERVICE_ENABLE_FUNCTION_TOOLS, "async_set_function_tools_enabled", "functions", True),
        (services.SERVICE_DISABLE_FUNCTION_TOOLS, "async_set_function_tools_enabled", "functions", False),
        (services.SERVICE_ENABLE_FUNCTION_GROUPS, "async_set_function_groups_enabled", "function_groups", True),
        (services.SERVICE_DISABLE_FUNCTION_GROUPS, "async_set_function_groups_enabled", "function_groups", False),
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
    entry = _entry()
    await _setup_entry(hass, entry)
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
        context=services.Context(user_id=hass_admin_user.id)
        if hasattr(services, "Context")
        else None,
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
            context=services.Context(user_id=hass_admin_user.id)
            if hasattr(services, "Context")
            else None,
        )


@pytest.mark.asyncio
async def test_guest_mode_services_dispatch_and_translate_manager_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_admin_user: MockUser,
) -> None:
    """Guest Mode actions must resolve one agent and preserve manager semantics."""
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

    manager.async_update_trusted.reset_mock()
    manager.async_update_trusted.side_effect = ValueError("end must follow start")
    with pytest.raises(HomeAssistantError, match="end must follow start"):
        await _response_service_call(
            hass,
            SERVICE_GUEST_MODE_UPDATE,
            {
                **base,
                "active_from": "2026-09-12T10:00:00+01:00",
                "active_until": "2026-09-12T09:00:00+01:00",
                "indefinite": False,
            },
            user_id=hass_admin_user.id,
        )
