"""OpenAI SDK platform metadata must be prepared outside the HA event loop."""

import asyncio
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import helpers


@pytest.mark.parametrize("azure", [False, True])
@pytest.mark.parametrize("skip_authentication", [False, True])
async def test_authenticated_client_primes_platform_headers_off_loop(
    monkeypatch, azure, skip_authentication
) -> None:
    """Cover both SDK clients, including the unauthenticated connection path."""
    loop_thread = get_ident()
    operations: list[tuple[str, int]] = []
    requests: list[dict] = []

    async def models():
        yield SimpleNamespace(id="model")

    def list_models(*, timeout: int):
        assert timeout == 10
        operations.append(("models", get_ident()))
        return models()

    def platform_headers():
        operations.append(("headers", get_ident()))
        return {"X-Stainless-OS": "Linux"}

    client = SimpleNamespace(
        platform_headers=platform_headers,
        models=SimpleNamespace(list=list_models),
    )

    def make_client(**kwargs):
        requests.append(kwargs)
        return client

    monkeypatch.setattr(helpers, "AsyncOpenAI", make_client)
    monkeypatch.setattr(helpers, "AsyncAzureOpenAI", make_client)
    monkeypatch.setattr(helpers, "get_async_client", lambda _hass: object())

    async def executor_job(function, *args):
        return await asyncio.to_thread(function, *args)

    hass = SimpleNamespace(async_add_executor_job=Mock(side_effect=executor_job))
    result = await helpers.get_authenticated_client(
        hass=hass,
        api_key="placeholder",
        base_url="https://test.openai.azure.com" if azure else None,
        api_version="2026-11-01" if azure else None,
        organization=None,
        api_provider="azure" if azure else "openai",
        skip_authentication=skip_authentication,
    )

    assert result is client
    assert len(requests) == 1
    assert ("azure_endpoint" in requests[0]) is azure
    assert [operation for operation, _thread in operations] == [
        "headers",
        *([] if skip_authentication else ["models"]),
    ]
    assert all(thread != loop_thread for _operation, thread in operations)
