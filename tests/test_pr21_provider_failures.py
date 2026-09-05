"""Regression tests for PR21 provider failure handling and diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
import pytest

from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIConversationConfigFlow,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import ParseArgumentsFailed
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
    classify_config_provider_error,
    ensure_successful_responses_result,
    provider_error_metadata,
    request_reauthentication,
)
from homeassistant.const import CONF_API_KEY


def _status_error(error_type, status: int, *, code: str = "provider_code"):
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    response = httpx.Response(
        status,
        request=request,
        headers={"x-request-id": "req_test_123"},
    )
    return error_type(
        "provider rejected request",
        response=response,
        body={"code": code, "type": "provider_type", "secret": "do-not-log"},
    )


def test_config_failure_classification_covers_status_and_connection_errors() -> None:
    assert classify_config_provider_error(_status_error(AuthenticationError, 401)) == "invalid_auth"
    assert classify_config_provider_error(APIConnectionError(request=httpx.Request("GET", "https://example.com"))) == "cannot_connect"
    assert classify_config_provider_error(_status_error(PermissionDeniedError, 403)) == "provider_forbidden"
    assert classify_config_provider_error(_status_error(RateLimitError, 429)) == "provider_rate_limited"
    assert classify_config_provider_error(_status_error(InternalServerError, 503)) == "provider_unavailable"


def test_provider_metadata_is_bounded_and_body_free() -> None:
    metadata = provider_error_metadata(
        _status_error(RateLimitError, 429, code="rate_limit_exceeded")
    )
    assert metadata["status_code"] == 429
    assert metadata["code"] == "rate_limit_exceeded"
    assert metadata["provider_request_id"] == "req_test_123"
    assert "body" not in metadata
    assert "secret" not in str(metadata)


async def test_config_and_reauth_use_same_provider_classification() -> None:
    error = _status_error(RateLimitError, 429)
    flow = SimpleNamespace(
        hass=MagicMock(),
        _reauth_entry=SimpleNamespace(data={CONF_API_KEY: "old"}),
        async_show_form=MagicMock(side_effect=lambda **kwargs: kwargs),
        async_create_entry=MagicMock(),
        async_update_reload_and_abort=MagicMock(),
    )
    with patch(
        "custom_components.extended_openai_conversation_responses.config_flow.validate_input",
        AsyncMock(side_effect=error),
    ):
        initial = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "key"}
        )
        reauth = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "replacement"}
        )
    assert initial["errors"]["base"] == "provider_rate_limited"
    assert reauth["errors"]["base"] == "provider_rate_limited"


class FakeStream:
    def __init__(self, events: list[object]) -> None:
        self._events = iter(events)
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration as err:
            raise StopAsyncIteration from err


def _entity() -> ExtendedOpenAIBaseLLMEntity:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    return entity


async def test_responses_failed_preserves_structured_provider_code() -> None:
    event = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(
            id="resp_123",
            error=SimpleNamespace(
                message="capacity unavailable",
                code="server_error",
                type="server_error",
            ),
        ),
    )
    with pytest.raises(ProviderStreamError) as caught:
        _ = [
            item
            async for item in _entity()._transform_responses_stream(
                SimpleNamespace(async_trace=lambda _value: None), FakeStream([event])
            )
        ]
    assert caught.value.code == "server_error"
    assert caught.value.type == "server_error"
    assert caught.value.response_id == "resp_123"


async def test_response_error_event_preserves_code() -> None:
    event = SimpleNamespace(
        type="response.error",
        message="rate limited",
        code="rate_limit_exceeded",
        request_id="req_stream_1",
    )
    with pytest.raises(ProviderStreamError) as caught:
        _ = [
            item
            async for item in _entity()._transform_responses_stream(
                SimpleNamespace(async_trace=lambda _value: None), FakeStream([event])
            )
        ]
    assert caught.value.code == "rate_limit_exceeded"
    assert caught.value.request_id == "req_stream_1"


def test_debug_provider_request_records_safe_failure_metadata() -> None:
    manager = DebugManager()
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={},
        incoming_conversation_id=None,
    )
    request = trace.start_provider_request("responses", (), {})
    request.finish(successful=False, error=_status_error(RateLimitError, 429))
    payload = request.as_dict()
    assert payload["error_type"] == "RateLimitError"
    assert payload["error"]["status_code"] == 429
    assert payload["error"]["provider_request_id"] == "req_test_123"
    assert "body" not in payload["error"]


def test_nonstream_responses_result_rejects_failed_and_incomplete_states() -> None:
    with pytest.raises(ProviderStreamError, match="failed") as failed:
        ensure_successful_responses_result(
            SimpleNamespace(
                id="resp_failed",
                status="failed",
                error=SimpleNamespace(message="bad", code="server_error", type="server_error"),
            )
        )
    assert failed.value.code == "server_error"
    with pytest.raises(ProviderStreamError, match="incomplete") as incomplete:
        ensure_successful_responses_result(
            SimpleNamespace(
                id="resp_incomplete",
                status="incomplete",
                error=None,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            )
        )
    assert incomplete.value.code == "max_output_tokens"


def test_runtime_authentication_failure_starts_reauth() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(async_start_reauth=MagicMock())
    assert request_reauthentication(
        hass, entry, _status_error(AuthenticationError, 401)
    ) is True
    entry.async_start_reauth.assert_called_once_with(hass)


class _Usage:
    def __init__(self) -> None:
        self.failed: list[str] = []
    def mark_current_run_failed(self, name: str) -> None:
        self.failed.append(name)


async def test_conversation_runtime_401_starts_reauth_and_returns_error() -> None:
    auth_error = _status_error(AuthenticationError, 401)
    entry = SimpleNamespace(async_start_reauth=MagicMock())
    entity = SimpleNamespace(
        hass=MagicMock(),
        entry=entry,
        subentry=SimpleNamespace(data={}),
        _usage=_Usage(),
        _get_exposed_entities=lambda: [],
        _get_function_tools=lambda: [],
        _async_retrieve_memories=AsyncMock(return_value=[]),
        _async_retrieve_temporary_memories=AsyncMock(return_value=[]),
        _build_system_prompt=lambda *_args: "system",
        _async_handle_chat_log=AsyncMock(side_effect=auth_error),
        _fire_conversation_finished=MagicMock(),
    )
    user_input = SimpleNamespace(
        language="en",
        conversation_id="conversation",
        text="hello",
        as_llm_context=lambda _domain: SimpleNamespace(),
    )
    chat_log = SimpleNamespace(content=[None], conversation_id="conversation")
    result = await ExtendedOpenAIAgentEntity._async_handle_message(
        entity, user_input, chat_log
    )
    entry.async_start_reauth.assert_called_once_with(entity.hass)
    assert entity._usage.failed == ["AuthenticationError"]
    assert result.conversation_id == "conversation"


def test_parse_arguments_error_no_longer_claims_token_exhaustion() -> None:
    message = str(ParseArgumentsFailed('{"broken":'))
    assert "malformed or unparseable" in message
    assert "maximum token" not in message.casefold()
    assert '{"broken":' not in message
