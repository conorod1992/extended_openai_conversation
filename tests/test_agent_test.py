"""Tests for the safe Test agent action."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import AuthenticationError, OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses.agent_test import (
    async_test_agent,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    CONF_WEB_SEARCH,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
)


def _objects(options: dict | None = None):
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Assistant",
        data={
            CONF_CHAT_MODEL: "gpt-4.1-mini",
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: "[]",
            CONF_MEMORY_MODE: MEMORY_MODE_OFF,
            **(options or {}),
        },
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1, total_tokens=5),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        )
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        runtime_data=client,
    )
    hass = MagicMock()
    usage = SimpleNamespace(async_record_request=AsyncMock())
    return hass, entry, subentry, client, usage


async def _run(options: dict | None = None, *, exposed: int = 1):
    hass, entry, subentry, client, usage = _objects(options)
    entities = [SimpleNamespace()] * exposed
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=entities,
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)
    return result, client, usage


async def test_successful_agent_test() -> None:
    result, client, usage = await _run()

    assert result.status == "Passed"
    assert result.authentication_rejected is False
    assert result.as_dict()["authentication_rejected"] is False
    assert {check.name for check in result.checks} >= {
        "Authentication",
        "Model access",
        "API mode",
        "Function calling",
        "Web Search",
        "Persistent memory",
        "Exposed entities",
        "Skills",
        "Configuration",
    }
    client.chat.completions.create.assert_awaited_once()
    usage.async_record_request.assert_awaited_once()


async def test_invalid_authentication() -> None:
    hass, entry, subentry, client, usage = _objects()
    request = MagicMock()
    response = MagicMock(status_code=401, request=request)
    authentication_error = AuthenticationError(
        "invalid key", response=response, body=None
    )
    client.chat.completions.create.side_effect = authentication_error
    reauth = MagicMock(return_value=True)
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.request_reauthentication",
            reauth,
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)

    assert result.status == "Failed"
    assert result.authentication_rejected is True
    assert result.as_dict()["authentication_rejected"] is True
    assert any(
        check.name == "Authentication" and check.status == "Failed"
        for check in result.checks
    )
    reauth.assert_called_once_with(hass, entry, authentication_error)


async def test_generic_openai_http_401_uses_runtime_reauth_rule() -> None:
    class Generic401Error(OpenAIError):
        status_code = 401

    hass, entry, subentry, client, usage = _objects()
    entry.async_start_reauth = MagicMock()
    generic_401 = Generic401Error("generic authentication rejection")
    client.chat.completions.create.side_effect = generic_401

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)

    assert result.status == "Failed"
    assert result.authentication_rejected is True
    entry.async_start_reauth.assert_called_once_with(hass)
    authentication = next(
        check for check in result.checks if check.name == "Authentication"
    )
    assert authentication.status == "Failed"
    assert any(
        check.name == "Model access" and check.message == "Authentication rejected"
        for check in result.checks
    )


async def test_reauthentication_is_requested_before_usage_recording() -> None:
    hass, entry, subentry, client, usage = _objects()
    response = MagicMock(status_code=401, request=MagicMock())
    authentication_error = AuthenticationError(
        "invalid key", response=response, body=None
    )
    client.chat.completions.create.side_effect = authentication_error
    usage.async_record_request.side_effect = RuntimeError("usage unavailable")
    reauth = MagicMock(return_value=True)

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.request_reauthentication",
            reauth,
        ),
        pytest.raises(RuntimeError, match="usage unavailable"),
    ):
        await async_test_agent(hass, entry, subentry)

    reauth.assert_called_once_with(hass, entry, authentication_error)


async def test_unsupported_api_mode_stops_before_probe() -> None:
    hass, entry, subentry, client, _ = _objects({CONF_API_MODE: "unknown"})
    result = await async_test_agent(hass, entry, subentry)

    assert result.status == "Failed"
    assert result.authentication_rejected is False
    assert result.checks[-1].name == "API mode"
    client.chat.completions.create.assert_not_awaited()


async def test_web_search_incompatibility_is_specific_failure() -> None:
    result, _, _ = await _run({CONF_WEB_SEARCH: True})

    assert result.status == "Failed"
    web = next(check for check in result.checks if check.name == "Web Search")
    assert web.status == "Failed"
    assert "does not support" in web.message


async def test_memory_unavailable_is_reported() -> None:
    hass, entry, subentry, _, usage = _objects({CONF_MEMORY_MODE: MEMORY_MODE_MANUAL})
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_memory",
            AsyncMock(side_effect=RuntimeError("storage unavailable")),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)

    memory = next(check for check in result.checks if check.name == "Persistent memory")
    assert result.status == "Failed"
    assert memory.status == "Failed"


async def test_no_exposed_entities_produces_partial_warning() -> None:
    result, _, _ = await _run(exposed=0)

    assert result.status == "Warning"
    entities = next(
        check for check in result.checks if check.name == "Exposed entities"
    )
    assert entities.status == "Warning"
    assert entities.message == "0"


async def test_responses_failed_status_is_reported() -> None:
    hass, entry, subentry, client, usage = _objects(
        {CONF_API_MODE: API_MODE_RESPONSES, CONF_CHAT_MODEL: "gpt-5.6"}
    )
    client.responses = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                id="resp_failed",
                status="failed",
                error=SimpleNamespace(
                    message="provider failed",
                    code="server_error",
                    type="server_error",
                ),
                usage=None,
            )
        )
    )
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)
    model = next(check for check in result.checks if check.name == "Model access")
    assert result.status == "Failed"
    assert result.authentication_rejected is False
    assert model.status == "Failed"
    assert "server_error" in model.message
    usage.async_record_request.assert_awaited_once_with(successful=False)


async def test_responses_incomplete_status_is_reported() -> None:
    hass, entry, subentry, client, usage = _objects(
        {CONF_API_MODE: API_MODE_RESPONSES, CONF_CHAT_MODEL: "gpt-5.6"}
    )
    client.responses = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                id="resp_incomplete",
                status="incomplete",
                error=None,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
            )
        )
    )
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[SimpleNamespace()],
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)
    model = next(check for check in result.checks if check.name == "Model access")
    assert result.status == "Failed"
    assert result.authentication_rejected is False
    assert model.status == "Failed"
    assert "max_output_tokens" in model.message
    usage.async_record_request.assert_awaited_once_with(successful=False)
