"""Failure and lifecycle contracts for remote model catalogue management."""

import asyncio
import json
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientError
import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)


class MemoryStore:
    """Minimal store that keeps persisted refresh state in memory."""

    def __init__(self):
        self.saved = None
        self.fail = False
        self.save_calls = 0

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, value):
        self.save_calls += 1
        if self.fail:
            raise OSError("disk full")
        self.saved = deepcopy(value)


@pytest.fixture
def manager(hass):
    result = runtime.ModelCatalogManager(hass)
    result.store = MemoryStore()
    return result


def candidate():
    """Return a valid downloaded catalogue that extends bundled reasoning."""
    value = deepcopy(runtime.BUNDLED_CATALOG)
    value["catalog_version"] += 1
    model = next(item for item in value["models"] if item["id"] == "gpt-5.6")
    model["reasoning_efforts"] = ["low", "medium", "high", "xhigh"]
    return value


def transport(monkeypatch, raw=b"", *, status=200, error=None, etag='"v2"'):
    async def chunks(_size):
        for offset in range(0, len(raw), 100):
            yield raw[offset : offset + 100]

    response = SimpleNamespace(
        status=status,
        headers={"ETag": etag} if etag else {},
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


async def set_saved_conversation(monkeypatch, manager, *, model="gpt-5.6", effort=None):
    """Expose one persisted conversation subentry through HA's config-entry shape."""
    data = {CONF_CHAT_MODEL: model}
    if effort is not None:
        data[CONF_REASONING_EFFORT] = effort
    subentry = SimpleNamespace(
        subentry_type="conversation",
        subentry_id="conversation-1",
        data=data,
    )
    entry = SimpleNamespace(entry_id="entry-1", subentries={"conversation-1": subentry})
    monkeypatch.setattr(manager.hass.config_entries, "async_entries", lambda _domain: [entry])

    async def no_rules(_hass, _entry_id, _subentry_id):
        return SimpleNamespace(snapshot=lambda: {"rules": []})

    monkeypatch.setattr(runtime, "async_get_request_rules", no_rules)


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


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("gpt-5.6", None, False),
        ("gpt-5.6", "high", False),
        ("gpt-5.6", "xhigh", True),
        ("gpt-5.6", 42, False),
        ("private-model", "xhigh", True),
    ],
    ids=[
        "no-reasoning",
        "bundled-compatible",
        "download-only-reasoning",
        "legacy-malformed-reasoning",
        "model-absent-from-bundled-catalog",
    ],
)
async def test_bundled_reset_safeguard_checks_saved_conversation_reasoning(
    manager, monkeypatch, model, effort, expected
):
    manager.catalog = candidate()
    await set_saved_conversation(
        monkeypatch, manager, model=model, effort=effort
    )

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is expected


async def test_public_reset_blocks_download_only_saved_reasoning(manager, monkeypatch):
    downloaded = candidate()
    manager.catalog = downloaded
    manager.etag = '"v2"'
    manager.store.saved = {
        "catalog": deepcopy(downloaded),
        "etag": manager.etag,
        "last_checked": manager.last_checked,
    }
    runtime.activate_catalog(downloaded)
    await set_saved_conversation(monkeypatch, manager, effort="xhigh")

    result = await manager.async_reset()

    assert result["source"] == "downloaded"
    assert result["last_error"]
    assert manager.catalog == downloaded
    assert manager.etag == '"v2"'
    assert manager.store.saved["catalog"] == downloaded


async def test_reset_persistence_failure_keeps_published_catalog_and_can_retry(
    manager, monkeypatch
):
    downloaded = candidate()
    manager.catalog = downloaded
    manager.etag = '"v2"'
    manager.store.saved = {
        "catalog": deepcopy(downloaded),
        "etag": manager.etag,
        "last_checked": manager.last_checked,
    }
    runtime.activate_catalog(downloaded)
    monkeypatch.setattr(manager.hass.config_entries, "async_entries", lambda _domain: [])
    manager.store.fail = True

    failed = await manager.async_reset()

    assert failed["source"] == "downloaded"
    assert failed["last_error"]
    assert manager.catalog == downloaded
    assert manager.etag == '"v2"'
    assert manager.store.saved["catalog"] == downloaded
    assert runtime.model_metadata("gpt-5.6")["reasoning_efforts"][-1] == "xhigh"

    manager.store.fail = False
    succeeded = await manager.async_reset()

    assert succeeded["source"] == "bundled"
    assert succeeded["last_error"] is None
    assert manager.catalog is None
    assert manager.etag is None
    assert manager.store.saved["catalog"] is None
    assert runtime.model_metadata("gpt-5.6")["reasoning_efforts"] == [
        "low",
        "medium",
        "high",
    ]


async def test_concurrent_ordinary_refreshes_share_one_fetch(manager, monkeypatch):
    raw = json.dumps(candidate()).encode()
    started = asyncio.Event()
    release = asyncio.Event()

    async def chunks(_size):
        started.set()
        await release.wait()
        yield raw

    response = SimpleNamespace(
        status=200,
        headers={"ETag": '"v2"'},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    context = AsyncMock()
    context.__aenter__.return_value = response
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )

    first = asyncio.create_task(manager.async_update())
    await started.wait()
    second = asyncio.create_task(manager.async_update())
    await asyncio.sleep(0)
    assert not second.done()

    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert get.call_count == 1
    assert first_result == second_result
    assert first_result["source"] == "downloaded"
    assert first_result["last_error"] is None
    assert manager.catalog == candidate()
    assert manager.store.saved["catalog"] == candidate()


async def test_failed_refresh_keeps_downloaded_catalog_and_forced_retry_succeeds(
    manager, monkeypatch
):
    downloaded = candidate()
    manager.catalog = deepcopy(downloaded)
    manager.etag = '"v2"'
    manager.store.saved = {
        "catalog": deepcopy(downloaded),
        "etag": manager.etag,
        "last_checked": 0.0,
    }
    runtime.activate_catalog(downloaded)
    failed_get = transport(monkeypatch, error=TimeoutError())

    failed = await manager.async_update()

    assert failed["source"] == "downloaded"
    assert failed["last_error"]
    assert manager.catalog == downloaded
    assert manager.store.saved["catalog"] == downloaded
    assert failed_get.call_count == 1

    refreshed = candidate()
    refreshed["catalog_version"] += 1
    refreshed["models"][0]["display_name"] += " refreshed"
    success_get = transport(monkeypatch, json.dumps(refreshed).encode(), etag='"v3"')

    succeeded = await manager.async_update(force=True)

    assert success_get.call_count == 1
    assert succeeded["source"] == "downloaded"
    assert succeeded["last_error"] is None
    assert manager.catalog == refreshed
    assert manager.etag == '"v3"'
    assert manager.store.saved["catalog"] == refreshed
