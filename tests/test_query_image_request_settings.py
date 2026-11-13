"""Image auxiliary budgets preserve explicit limits and provider wire contracts."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import request, services
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_service_handlers import _call, _handlers


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "model,effort,budget",
    [
        ("gpt-4.1", None, 300),
        ("gpt-5", "minimal", 8192),
        ("gpt-5.6", "low", 8192),
        ("o3-pro", None, 8192),
    ],
)
@pytest.mark.parametrize("explicit", [None, 16, 300])
async def test_image_query_defaults_and_explicit_limits(
    hass, monkeypatch, mode, model, effort, budget, explicit
):
    raw = {
        "id": "response",
        "provider_extra": {"untouched": [1, 2]},
        "usage": {"total_tokens": 27},
    }
    response = SimpleNamespace(
        status="completed",
        choices=[SimpleNamespace(finish_reason="stop")],
        model_dump=lambda: raw,
    )
    create = AsyncMock(return_value=response)
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=services.DOMAIN,
        state=ConfigEntryState.LOADED,
        data={},
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=create),
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        ),
    )
    data = {
        "config_entry": "entry",
        "model": model,
        "api_mode": mode,
        "prompt": "Describe",
        "images": [{"url": "https://example.test/image.png"}],
    }
    if explicit is not None:
        data["max_tokens"] = explicit
    # The schema must leave omission intact; explicit 300 remains explicit.
    validated = services.QUERY_IMAGE_SCHEMA(data)
    assert ("max_tokens" in validated) is (explicit is not None)
    handler = (await _handlers(hass))["query_image"]
    if model == "o3-pro" and mode == "chat_completions":
        with pytest.raises(ValueError, match="does not support chat_completions"):
            await handler(_call(validated))
        create.assert_not_awaited()
        return
    assert await handler(_call(validated)) is raw
    create.assert_awaited_once()
    sent = create.await_args.kwargs
    assert sent[
        "max_output_tokens" if mode == "responses" else "max_completion_tokens"
    ] == (budget if explicit is None else explicit)
    if effort is None:
        assert "reasoning" not in sent and "reasoning_effort" not in sent
    elif mode == "responses":
        assert sent["reasoning"] == {"effort": effort}
    else:
        assert sent["reasoning_effort"] == effort
    assert not {"tools", "temperature", "include", "service_tier"} & sent.keys()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
async def test_incomplete_answers_do_not_retry_or_reauthenticate(
    hass, monkeypatch, mode
):
    response = SimpleNamespace(
        status="incomplete",
        incomplete_details={"reason": "max_output_tokens"},
        choices=[SimpleNamespace(finish_reason="length")],
        model_dump=Mock(),
    )
    create = AsyncMock(return_value=response)
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=services.DOMAIN,
        state=ConfigEntryState.LOADED,
        data={},
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=create),
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        ),
    )
    reauth = Mock()
    monkeypatch.setattr(services, "request_reauthentication", reauth)
    handler = (await _handlers(hass))["query_image"]
    with pytest.raises(HomeAssistantError, match=r"output token limit.*completing"):
        await handler(
            _call(
                {
                    "config_entry": "entry",
                    "model": "gpt-5",
                    "api_mode": mode,
                    "prompt": "Describe",
                    "images": [{"url": "https://example.test/image.png"}],
                    "max_tokens": 16,
                }
            )
        )
    create.assert_awaited_once()
    reauth.assert_not_called()
    response.model_dump.assert_not_called()


def test_other_incomplete_status_has_clear_error():
    with pytest.raises(HomeAssistantError, match="incomplete response"):
        services._ensure_complete_image_result(
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "content_filter"},
            },
            "responses",
        )


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_defaults_respect_model_ceiling_and_api_effort_support(monkeypatch, mode):
    caps = deepcopy(request.get_model_capabilities("gpt-5"))
    caps["limits"]["max_output_tokens"] = 1024
    caps["reasoning"]["by_api"][mode]["efforts"] = []
    monkeypatch.setattr(request, "request_model_capabilities", lambda *_: caps)
    settings = request.query_image_request_settings("deployment", mode, {}, {})
    assert settings == {
        "max_output_tokens" if mode == "responses" else "max_completion_tokens": 1024
    }
    # A low explicitly supplied budget must never be inflated to the default.
    assert list(
        request.query_image_request_settings(
            "deployment", mode, {"max_tokens": 16}, {}
        ).values()
    ) == [16]


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_azure_deployment_uses_underlying_model(mode):
    settings = request.query_image_request_settings(
        "deployment", mode, {"azure_model": "gpt-5"}, {"api_provider": "azure"}
    )
    assert settings == (
        {"max_output_tokens": 8192, "reasoning": {"effort": "minimal"}}
        if mode == "responses"
        else {"max_completion_tokens": 8192, "reasoning_effort": "minimal"}
    )


def test_unknown_compatible_model_has_legacy_token_field_and_no_guessed_reasoning():
    assert request.query_image_request_settings(
        "gpt-5-private",
        "chat_completions",
        {},
        {"api_provider": "custom", "base_url": "https://example.test/v1"},
    ) == {"max_tokens": 300}


def test_service_yaml_leaves_token_limit_unset():
    path = Path(services.__file__).with_name("services.yaml")
    assert (
        "default"
        not in yaml.safe_load(path.read_text())["query_image"]["fields"]["max_tokens"]
    )


@pytest.mark.parametrize("mode,limit", [("responses", 1), ("chat_completions", 0)])
def test_explicit_budgets_below_api_minimum_are_rejected_not_inflated(mode, limit):
    with pytest.raises(HomeAssistantError, match="token limit"):
        request.query_image_request_settings("gpt-5", mode, {"max_tokens": limit}, {})
