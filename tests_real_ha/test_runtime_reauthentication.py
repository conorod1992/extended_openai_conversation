"""Runtime provider authentication recovery through Home Assistant reauth."""

from __future__ import annotations

from threading import get_ident
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from custom_components.extended_openai_conversation_responses import (
    config_flow as config_flow_module,
    helpers,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    DOMAIN,
)
from homeassistant.components import conversation
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntries, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _responses_sse_text,
    _say,
    _speech,
)

_REPLACEMENT_KEY = "sk-runtime-reauth-replacement"
_UNAUTHORIZED = {
    "error": {
        "message": "Incorrect API key provided for runtime reauth acceptance",
        "type": "invalid_request_error",
        "param": None,
        "code": "invalid_api_key",
    }
}


@pytest.mark.parametrize("skip_authentication", [False, True])
async def test_sdk_preparation_uses_native_ha_executor(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, skip_authentication: bool
) -> None:
    """Real HA prepares the one shared-transport SDK client before wrappers."""
    loop_thread = get_ident()
    prepared = []
    requests = []
    prepare = helpers._prepare_openai_client

    def track(client):
        assert get_ident() != loop_thread
        prepare(client)
        prepared.append(client)

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    monkeypatch.setattr(helpers, "_prepare_openai_client", track)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as shared:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared)
        client = await helpers.get_authenticated_client(
            hass, "test-key", "https://mock.invalid/v1", None, None, "openai",
            skip_authentication=skip_authentication,
        )
        assert prepared == [client]
        assert {"responses", "chat", "embeddings", "models"} <= vars(client).keys()
        assert "completions" in vars(client.chat)
        assert client._client is shared and not shared.is_closed
        assert len(requests) == (0 if skip_authentication else 1)


class _SuccessfulAuthenticatedWire:
    """Capture the replacement client's auth header and return one SDK response."""

    def __init__(self, reply: bytes) -> None:
        self._reply = reply
        self.requests: list[dict[str, str | None]] = []

    async def send(
        self, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        del args, kwargs
        self.requests.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
            }
        )
        assert len(self.requests) == 1, "Unexpected extra OpenAI SDK request"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=self._reply,
            request=request,
        )


@pytest.mark.parametrize(
    ("api_mode", "success_reply", "expected_path"),
    [
        (
            API_MODE_CHAT_COMPLETIONS,
            _chat_sse_text("Reauthentication recovered Chat Completions."),
            "/v1/chat/completions",
        ),
        (
            API_MODE_RESPONSES,
            _responses_sse_text("Reauthentication recovered Responses."),
            "/v1/responses",
        ),
    ],
)
async def test_runtime_401_drives_native_ha_reauthentication_and_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    success_reply: bytes,
    expected_path: str,
) -> None:
    """A real provider 401 must lead through HA reauth to a fresh working client."""
    original_agent = await _agent(hass, api_mode)
    assert original_agent is not None
    entry = original_agent.entry
    entry_id = entry.entry_id
    original_key = entry.data[CONF_API_KEY]

    unauthorized_wire = _install_wire(
        monkeypatch,
        original_agent,
        [(401, _UNAUTHORIZED)],
    )

    failed = await _say(hass, original_agent)

    assert failed.response.error_code is not None
    failed_speech = failed.response.as_dict()["speech"]["plain"]["speech"]
    assert "problem talking to OpenAI" in failed_speech
    assert "Provider authentication failed" in failed_speech
    assert "Incorrect API key provided" not in failed_speech
    assert len(unauthorized_wire.requests) == 1
    assert unauthorized_wire.requests[0]["path"] == expected_path
    assert entry.data[CONF_API_KEY] == original_key
    assert entry.state is ConfigEntryState.LOADED

    # ConfigEntry.async_start_reauth() schedules the native HA flow from the runtime
    # error handler. Let that task reach the integration's real reauth_confirm step.
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH, "entry_id": entry_id},
    )
    assert len(flows) == 1
    reauth = flows[0]
    # Progress snapshots are not FlowResult objects and therefore intentionally do
    # not carry a `type` field. The active step/context are the public proof that the
    # native reauthentication flow reached our confirmation form.
    assert reauth["step_id"] == "reauth_confirm"
    assert reauth["context"]["source"] == SOURCE_REAUTH
    assert reauth["context"]["entry_id"] == entry_id

    authenticate = AsyncMock(return_value=object())
    monkeypatch.setattr(
        config_flow_module,
        "get_authenticated_client",
        authenticate,
    )

    reloads = []
    reload_entry = ConfigEntries.async_reload

    async def count_reload(manager, changed_entry_id):
        reloads.append(changed_entry_id)
        return await reload_entry(manager, changed_entry_id)

    monkeypatch.setattr(ConfigEntries, "async_reload", count_reload)
    result = await hass.config_entries.flow.async_configure(
        reauth["flow_id"],
        {CONF_API_KEY: _REPLACEMENT_KEY},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["api_key"] == _REPLACEMENT_KEY

    await hass.async_block_till_done()
    assert reloads == [entry_id]
    current_entry = hass.config_entries.async_get_entry(entry_id)
    assert current_entry is entry
    assert current_entry.data[CONF_API_KEY] == _REPLACEMENT_KEY
    assert current_entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"source": SOURCE_REAUTH, "entry_id": entry_id},
    )

    replacement_agent = conversation.async_get_agent(hass, entry_id)
    assert replacement_agent is not None
    assert replacement_agent is not original_agent

    authenticated_wire = _SuccessfulAuthenticatedWire(success_reply)
    monkeypatch.setattr(
        _raw_client(replacement_agent)._client,
        "send",
        authenticated_wire.send,
    )

    recovered = await _say(hass, replacement_agent)

    assert _speech(recovered).startswith("Reauthentication recovered")
    assert authenticated_wire.requests == [
        {
            "path": expected_path,
            "authorization": f"Bearer {_REPLACEMENT_KEY}",
        }
    ]
    assert conversation.async_get_agent(hass, entry_id) is replacement_agent
