"""Actual dependency transport and public error boundaries contain opaque canaries."""

import json
import logging
from unittest.mock import AsyncMock

from openai import OpenAIError

from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.provider_errors import (
    provider_error_metadata,
    provider_user_message,
)
from custom_components.extended_openai_conversation_responses.public_errors import (
    sanitize_backend_error,
)
from homeassistant.exceptions import HomeAssistantError


def test_provider_echo_and_opaque_metadata_are_not_public():
    secret = "ordinary unfamiliar credential 42"
    error = OpenAIError(secret)
    error.code = secret
    error.request_id = secret
    error.response_id = secret
    assert secret not in json.dumps(provider_error_metadata(error))
    assert secret not in provider_user_message(error)
    assert (
        str(sanitize_backend_error(HomeAssistantError(secret)))
        == "Function execution failed. Check the tool configuration and permissions."
    )


async def test_actual_ha_rest_adapter_has_no_url_logging(hass, monkeypatch, caplog):
    secret = "unfamiliar_FABRICATED_9b2d7a"
    url = "https://example.invalid/private/" + secret
    requests = []

    class Response:
        charset = "utf-8"
        status = 200
        text = AsyncMock(return_value='{"ok":true}')

        def __init__(self):
            self.headers = {"Content-Type": "application/json"}

    class Request:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_):
            pass

    class Session:
        def request(self, method, resource, **kwargs):
            requests.append((method, resource, kwargs))
            return Request()

    monkeypatch.setattr(web, "_install_bounded_session", lambda *_: None)
    data = web.get_rest_data(hass, {"resource": url, "params": {"enabled": True}}, {})
    data._session = Session()
    with caplog.at_level(logging.DEBUG):
        await data.async_update(log_errors=True)
    assert requests[0][1] == url
    assert requests[0][2]["params"] == {"enabled": "true"}
    assert data.data == '{"ok":true}'
    assert secret not in caplog.text
    assert "status=200" in caplog.text
