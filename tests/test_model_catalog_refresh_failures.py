"""Failure contracts for remote model catalogue refreshes."""

import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientError
import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)


class MemoryStore:
    """Minimal store that keeps persisted refresh state in memory."""

    def __init__(self):
        self.saved = None

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, value):
        self.saved = deepcopy(value)


@pytest.fixture
def manager(hass):
    result = runtime.ModelCatalogManager(hass)
    result.store = MemoryStore()
    return result


def transport(monkeypatch, *, status=200, error=None):
    async def chunks(_size):
        if False:
            yield b""

    response = SimpleNamespace(
        status=status,
        headers={},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    context = AsyncMock()
    context.__aenter__.return_value = response
    if error is not None:
        context.__aenter__.side_effect = error
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )
    return get


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (200, TimeoutError()),
        (200, ClientError("offline")),
        (429, None),
        (503, None),
    ],
    ids=["timeout", "client-error", "rate-limit", "server-error"],
)
async def test_transient_refresh_failures_do_not_warn_or_error(
    manager, monkeypatch, caplog, status, error
):
    get = transport(monkeypatch, status=status, error=error)

    with caplog.at_level(logging.DEBUG, logger=runtime.__name__):
        result = await manager.async_update()

    assert result["last_error"]
    assert result["source"] == "bundled"
    assert manager.store.saved["catalog"] is None
    assert not [
        record
        for record in caplog.records
        if record.name == runtime.__name__ and record.levelno >= logging.WARNING
    ]

    # A background outage is remembered, so the hourly scheduler does not retry
    # until the normal daily refresh interval has elapsed.
    await manager.async_update()
    assert get.call_count == 1


async def test_permanent_http_failure_retains_warning_visibility(
    manager, monkeypatch, caplog
):
    transport(monkeypatch, status=404)

    with caplog.at_level(logging.WARNING, logger=runtime.__name__):
        result = await manager.async_update()

    assert result["last_error"]
    assert result["source"] == "bundled"
    assert any(
        record.name == runtime.__name__ and record.levelno == logging.WARNING
        for record in caplog.records
    )
