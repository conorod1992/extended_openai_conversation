"""Runtime provider authentication recovery through Home Assistant reauth."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from custom_components.extended_openai_conversation_responses import (
    config_flow as config_flow_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    DOMAIN,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH
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
    assert "Incorrect API key provided" in failed_speech
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

    result = await hass.config_entries.flow.async_configure(
        reauth["flow_id"],
        {CONF_API_KEY: _REPLACEMENT_KEY},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["api_key"] == _REPLACEMENT_KEY

    # async_update_reload_and_abort() keeps the same config entry identity but reloads
    # its platforms. The post-reauth runtime must therefore be a newly created agent.
    await hass.async_block_till_done()
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
