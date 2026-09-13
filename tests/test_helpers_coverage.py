"""Focused behavioral coverage for helpers.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, sentinel

import pytest

from custom_components.extended_openai_conversation_responses import helpers
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderTransportError,
)


def test_get_exposed_entities_filters_exposure_and_active_user(hass, monkeypatch) -> None:
    exposed = SimpleNamespace(
        entity_id="light.kitchen",
        name="Kitchen",
        state="on",
    )
    hidden = SimpleNamespace(
        entity_id="light.private",
        name="Private",
        state="off",
    )
    hass.states.async_all.return_value = [exposed, hidden]
    monkeypatch.setattr(
        helpers,
        "async_should_expose",
        lambda _hass, _domain, entity_id: entity_id == "light.kitchen",
    )
    monkeypatch.setattr(
        helpers,
        "get_entity_prompt_metadata",
        lambda _hass, entity_id: SimpleNamespace(aliases=("Cooking", "Main light")),
    )
    filtered = Mock(return_value=[{"entity_id": "light.kitchen", "allowed": True}])
    monkeypatch.setattr(helpers, "filter_entities_for_active_user", filtered)

    result = helpers.get_exposed_entities(hass)

    filtered.assert_called_once_with(
        hass,
        [
            {
                "entity_id": "light.kitchen",
                "name": "Kitchen",
                "state": "on",
                "aliases": ["Cooking", "Main light"],
            }
        ],
    )
    assert result == [{"entity_id": "light.kitchen", "allowed": True}]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (None, False),
        ("https://example.com/v1", False),
        ("https://example.openai.azure.com", True),
        ("https://example.azure-api.net/openai", True),
        ("https://example.services.ai.azure.com/openai", True),
    ],
)
def test_is_azure_url_recognizes_supported_azure_hosts(base_url, expected) -> None:
    assert helpers.is_azure_url(base_url) is expected


@pytest.mark.parametrize(
    ("provider", "base_url", "expected"),
    [
        (None, None, True),
        (helpers.DEFAULT_API_PROVIDER, helpers.DEFAULT_CONF_BASE_URL, True),
        (helpers.DEFAULT_API_PROVIDER, helpers.DEFAULT_CONF_BASE_URL + "/", True),
        ("azure", helpers.DEFAULT_CONF_BASE_URL, False),
        (helpers.DEFAULT_API_PROVIDER, "https://proxy.example/v1", False),
    ],
)
def test_supports_openai_hosted_tools_requires_native_openai_endpoint(
    provider, base_url, expected
) -> None:
    assert helpers.supports_openai_hosted_tools(provider, base_url) is expected


def test_convert_to_template_recurses_through_nested_dicts_and_lists(
    hass, monkeypatch
) -> None:
    created: list[tuple[str, object]] = []

    class FakeTemplate:
        def __init__(self, value, template_hass):
            self.value = value
            self.hass = template_hass
            created.append((value, template_hass))

    monkeypatch.setattr(helpers, "Template", FakeTemplate)
    settings = {
        "service": "{{ service_name }}",
        "data": {
            "message": "{{ message }}",
            "nested": {"plain": "{{ nested }}"},
            "items": [
                {"value": "{{ first }}"},
                {"deeper": [{"value": "{{ second }}"}]},
            ],
        },
        "outside": {"plain": "leave me alone"},
    }

    helpers.convert_to_template(settings, hass=hass)

    assert isinstance(settings["service"], FakeTemplate)
    assert isinstance(settings["data"]["message"], FakeTemplate)
    assert isinstance(settings["data"]["nested"]["plain"], FakeTemplate)
    assert isinstance(settings["data"]["items"][0]["value"], FakeTemplate)
    assert isinstance(settings["data"]["items"][1]["deeper"][0]["value"], FakeTemplate)
    assert settings["outside"]["plain"] == "leave me alone"
    assert all(template_hass is hass for _, template_hass in created)


def test_convert_to_template_honors_custom_keys_and_top_level_list(hass, monkeypatch) -> None:
    class FakeTemplate:
        def __init__(self, value, template_hass):
            self.value = value
            self.hass = template_hass

    monkeypatch.setattr(helpers, "Template", FakeTemplate)
    settings = [
        {"custom": "{{ one }}", "other": "plain"},
        {"nested": {"custom": "{{ two }}"}},
    ]

    helpers.convert_to_template(settings, template_keys=["custom"], hass=hass)

    assert isinstance(settings[0]["custom"], FakeTemplate)
    assert settings[0]["other"] == "plain"
    assert isinstance(settings[1]["nested"]["custom"], FakeTemplate)


class _AsyncPage:
    def __init__(self, values):
        self._values = list(values)

    def __aiter__(self):
        async def iterate():
            for value in self._values:
                yield value

        return iterate()


def _fake_client(page=None):
    return SimpleNamespace(
        models=SimpleNamespace(list=Mock(return_value=page or _AsyncPage([object()])))
    )


@pytest.mark.asyncio
async def test_authenticated_client_builds_azure_client_and_can_skip_authentication(
    hass, monkeypatch
) -> None:
    client = _fake_client()
    azure_factory = Mock(return_value=client)
    standard_factory = Mock()
    monkeypatch.setattr(helpers, "AsyncAzureOpenAI", azure_factory)
    monkeypatch.setattr(helpers, "AsyncOpenAI", standard_factory)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: sentinel.http_client)

    result = await helpers.get_authenticated_client(
        hass,
        api_key="secret",
        base_url="https://example.openai.azure.com",
        api_version="2026-01-01",
        organization="org",
        api_provider="azure",
        skip_authentication=True,
    )

    assert result is client
    standard_factory.assert_not_called()
    azure_factory.assert_called_once_with(
        api_key="secret",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2026-01-01",
        organization="org",
        http_client=sentinel.http_client,
    )
    client.models.list.assert_not_called()


@pytest.mark.asyncio
async def test_authenticated_client_builds_standard_client_and_validates_models(
    hass, monkeypatch
) -> None:
    page = _AsyncPage(["first", "second"])
    client = _fake_client(page)
    standard_factory = Mock(return_value=client)
    azure_factory = Mock()
    monkeypatch.setattr(helpers, "AsyncOpenAI", standard_factory)
    monkeypatch.setattr(helpers, "AsyncAzureOpenAI", azure_factory)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: sentinel.http_client)
    executor = AsyncMock(side_effect=lambda func: func())
    monkeypatch.setattr(hass, "async_add_executor_job", executor)

    result = await helpers.get_authenticated_client(
        hass,
        api_key="secret",
        base_url="https://proxy.example/v1",
        api_version=None,
        organization=None,
        api_provider=helpers.DEFAULT_API_PROVIDER,
    )

    assert result is client
    azure_factory.assert_not_called()
    standard_factory.assert_called_once_with(
        api_key="secret",
        base_url="https://proxy.example/v1",
        organization=None,
        http_client=sentinel.http_client,
    )
    client.models.list.assert_called_once_with(timeout=10)
    executor.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("slow"), ConnectionError("offline")])
async def test_authenticated_client_normalizes_transport_failures(
    hass, monkeypatch, error
) -> None:
    client = _fake_client()
    monkeypatch.setattr(helpers, "AsyncOpenAI", Mock(return_value=client))
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: sentinel.http_client)
    monkeypatch.setattr(
        hass,
        "async_add_executor_job",
        AsyncMock(side_effect=error),
    )

    with pytest.raises(ProviderTransportError) as exc_info:
        await helpers.get_authenticated_client(
            hass,
            api_key="secret",
            base_url=None,
            api_version=None,
            organization=None,
            api_provider=helpers.DEFAULT_API_PROVIDER,
        )

    assert exc_info.value.__cause__ is error
