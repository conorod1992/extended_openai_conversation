"""Actual dependency transport and public error boundaries contain opaque canaries."""

import json
import logging
from unittest.mock import AsyncMock

from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses.exceptions import (
    EffectReplayBlocked,
    FunctionResponseParseError,
)
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.provider_errors import (
    provider_error_metadata,
    provider_user_message,
)
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedRequestError,
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


def test_only_bounded_http_status_survives_public_error_boundary():
    error = SanitizedRequestError(403)
    assert sanitize_backend_error(error) is error
    assert str(error) == "REST request failed: HTTP 403 Forbidden"
    assert "987654321" not in str(SanitizedRequestError(987654321))


@pytest.mark.parametrize(
    "error_type", [EffectReplayBlocked, FunctionResponseParseError]
)
def test_control_flow_types_survive_without_arbitrary_error_content(error_type):
    error = sanitize_backend_error(error_type("unfamiliar private credential 42"))
    assert isinstance(error, error_type)
    assert "unfamiliar" not in str(error)


@pytest.mark.parametrize("auth_layout", ["legacy", "basic", "digest"])
async def test_actual_ha_rest_adapter_has_no_url_logging(
    hass, monkeypatch, caplog, auth_layout
):
    secret = "unfamiliar_FABRICATED_9b2d7a"
    url = "https://example.invalid/private/" + secret
    requests = []

    class Response:
        charset = "utf-8"
        status = 200
        text = AsyncMock(return_value=secret)

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
    digest = object()
    if auth_layout != "legacy":
        if hasattr(data, "_auth"):
            del data._auth
        data._basic_auth = "Basic fabricated" if auth_layout == "basic" else None
        data._digest_auth = digest if auth_layout == "digest" else None
    data._session = Session()
    with caplog.at_level(logging.DEBUG):
        await data.async_update(log_errors=True)
        assert await hass.async_add_executor_job(data.data_without_xml) == secret
    assert requests[0][1] == url
    assert requests[0][2]["params"] == {"enabled": "true"}
    if auth_layout == "basic":
        assert requests[0][2]["headers"]["authorization"] == "Basic fabricated"
    elif auth_layout == "digest":
        assert requests[0][2]["middlewares"] == (digest,)
    assert data.data == secret
    assert secret not in caplog.text
    assert "status=200" in caplog.text
