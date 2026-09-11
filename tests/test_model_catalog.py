"""Model capability catalog v2 parsing, lifecycle, and fail-safe refresh tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
    model_catalog_manager as runtime,
)


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(data, "_active", data.BUNDLED_CATALOG)


def candidate():
    value = deepcopy(data.BUNDLED_CATALOG)
    value["catalog_version"] += 1
    value["models"][0]["display_name"] = "Astra (catalog update)"
    return value


class MemoryStore:
    def __init__(self):
        self.saved = None
        self.fail = False

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, value):
        if self.fail:
            raise OSError("disk full")
        self.saved = deepcopy(value)


@pytest.fixture
def manager(hass):
    result = runtime.ModelCatalogManager(hass)
    result.store = MemoryStore()
    return result


def transport(monkeypatch, raw, *, status=200, etag='"v3"', error=None):
    async def chunks(_size):
        for offset in range(0, len(raw), 100):
            yield raw[offset : offset + 100]

    response = SimpleNamespace(
        status=status,
        headers={"ETag": etag},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    context = AsyncMock()
    context.__aenter__.return_value = response
    if error:
        context.__aenter__.side_effect = error
    session = SimpleNamespace(get=Mock(return_value=context))
    monkeypatch.setattr(runtime, "async_get_clientsession", lambda _: session)
    return session.get


def test_bundled_catalog_is_schema_v2_and_parses_exactly():
    parsed = data.parse_catalog(Path(data.__file__).with_suffix(".json").read_bytes())
    assert parsed == data.BUNDLED_CATALOG
    assert parsed["schema_version"] == 2
    assert parsed["catalog_version"] >= 2


def test_required_current_models_and_invalid_aliases():
    expected = {
        "gpt-6-astra",
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "o3",
    }
    current = {
        item["id"]
        for item in data.BUNDLED_CATALOG["models"]
        if item["status"] == "current"
    }
    assert expected <= current
    assert not {"o2", "o4", "gpt-5.3"} & current


def test_picker_hides_deprecated_unless_already_selected():
    normal = data.catalog_picker_models()
    assert all(item["status"] == "current" for item in normal)
    assert "gpt-4-turbo" not in {item["id"] for item in normal}

    existing = data.catalog_picker_models("gpt-4-turbo")
    deprecated = next(item for item in existing if item["id"] == "gpt-4-turbo")
    assert deprecated["status"] == "deprecated"


def test_unknown_model_is_preserved_with_conservative_capabilities():
    metadata = data.model_metadata("my-future-openai-model")
    assert metadata["id"] == "my-future-openai-model"
    assert metadata["status"] == "unknown"
    assert metadata["temperature"]["support"] == "undocumented"
    assert metadata["top_p"]["support"] == "undocumented"
    assert metadata["function_calling"]["responses"] is False
    assert metadata["function_calling"]["chat_completions"] is False


def test_every_current_model_supports_streaming():
    for item in data.BUNDLED_CATALOG["models"]:
        if item["status"] == "current":
            assert item["streaming"] is True, item["id"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(schema_version=1),
        lambda v: v.update(schema_version=True),
        lambda v: v.update(catalog_version="next"),
        lambda v: v.update(models=[]),
        lambda v: v["models"].append(deepcopy(v["models"][0])),
        lambda v: v["models"][0].update(status="retired"),
        lambda v: v["models"][0]["temperature"].update(support="maybe"),
        lambda v: v["models"][0]["temperature"].update(send_policy="send"),
        lambda v: v["models"][0]["reasoning"].update(efforts=["run code"]),
        lambda v: v["models"][0]["api"].update(responses=1),
        lambda v: v["models"][0]["limits"].update(max_output_tokens=0),
    ],
)
def test_invalid_schema_v2_catalog_is_rejected(mutate):
    value = candidate()
    mutate(value)
    with pytest.raises(ValueError):
        data.validate_catalog(value)


async def test_download_updates_consumers_and_persists(manager, monkeypatch):
    value = candidate()
    get = transport(monkeypatch, json.dumps(value).encode())
    result = await manager.async_update(force=True)
    assert result["source"] == "downloaded"
    assert result["schema_version"] == 2
    assert result["last_error"] is None
    assert get.call_args.kwargs == {"headers": {}, "allow_redirects": False}
    assert manager.store.saved["catalog"] == value
    assert data.model_metadata("gpt-6-astra")["display_name"] == "Astra (catalog update)"


async def test_stored_v1_catalog_is_migrated_to_authoritative_v2(manager):
    manager.store.saved = {
        "catalog": {
            "schema_version": 1,
            "catalog_version": 1,
            "defaults": {},
            "models": [],
        },
        "etag": '"old"',
        "last_checked": 0,
    }
    await manager.async_load()
    assert manager.status()["schema_version"] == 2
    assert data.model_metadata("gpt-5.6")["reasoning"]["efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b'{"schema_version":2,"schema_version":2}',
        b" " * (data.MAX_CATALOG_BYTES + 1),
    ],
    ids=["truncated", "array", "duplicate-key", "oversized"],
)
async def test_malformed_and_oversized_download_rejected(manager, monkeypatch, raw):
    transport(monkeypatch, raw)
    assert (await manager.async_update(force=True))["last_error"]
    assert manager.catalog is None
    assert data.model_metadata("gpt-5.6")["status"] == "current"


@pytest.mark.parametrize("failure", ["network", "http", "storage", "old", "same-version"])
async def test_failed_update_never_replaces_current_data(manager, monkeypatch, failure):
    first = candidate()
    transport(monkeypatch, json.dumps(first).encode())
    await manager.async_update(force=True)
    previous = deepcopy(manager.catalog)

    value = deepcopy(first)
    value["catalog_version"] += 1
    value["models"][0]["display_name"] = "Another label"
    if failure == "old":
        value["catalog_version"] = data.BUNDLED_CATALOG["catalog_version"] - 1
    elif failure == "same-version":
        value["catalog_version"] = previous["catalog_version"]
    elif failure == "storage":
        manager.store.fail = True

    transport(
        monkeypatch,
        json.dumps(value).encode(),
        status=500 if failure == "http" else 200,
        error=TimeoutError() if failure == "network" else None,
    )
    assert (await manager.async_update(force=True))["last_error"]
    assert manager.catalog == previous
    if not manager.store.fail:
        assert manager.store.saved["catalog"] == previous


async def test_daily_cadence_etag_and_manual_reset(manager, monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(runtime.time, "time", lambda: now)
    get = transport(monkeypatch, json.dumps(candidate()).encode())
    await manager.async_update()
    now += runtime.UPDATE_INTERVAL - 1
    await manager.async_update()
    assert get.call_count == 1

    now += 1
    get = transport(monkeypatch, b"", status=304)
    await manager.async_update()
    assert get.call_args.kwargs["headers"] == {"If-None-Match": '"v3"'}
    assert manager.status()["last_error"] is None

    assert (await manager.async_reset())["source"] == "bundled"
    assert manager.catalog is None
    assert manager.etag is None


async def test_corrupt_storage_falls_back_to_bundled(manager):
    manager.store.saved = {"catalog": {"schema_version": 99}}
    await manager.async_load()
    assert manager.status()["source"] == "bundled"
    assert manager.status()["schema_version"] == 2
    assert manager.last_error


async def test_reset_waits_for_inflight_update(manager, monkeypatch):
    transport(monkeypatch, json.dumps(candidate()).encode())
    started, release = asyncio.Event(), asyncio.Event()
    save = manager.store.async_save

    async def paused_save(value):
        started.set()
        await release.wait()
        await save(value)

    monkeypatch.setattr(manager.store, "async_save", paused_save)
    update = asyncio.create_task(manager.async_update(force=True))
    await started.wait()
    assert data.model_metadata("gpt-6-astra")["display_name"] == "gpt-6-astra"
    reset = asyncio.create_task(manager.async_reset())
    release.set()
    await asyncio.gather(update, reset)
    assert manager.catalog is None
    assert manager.store.saved["catalog"] is None


async def test_setup_is_shared_and_timer_is_removed_on_stop(hass, monkeypatch):
    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    monkeypatch.setattr(runtime, "ModelCatalogManager", lambda _: manager)
    interval, cancel = Mock(), Mock()
    interval.return_value = cancel
    monkeypatch.setattr(runtime, "async_track_time_interval", interval)
    monkeypatch.setattr(runtime.websocket_api, "async_register_command", Mock())
    await runtime.async_setup_model_catalog(hass)
    await runtime.async_setup_model_catalog(hass)
    assert interval.call_count == 1
    assert interval.call_args.args[2].total_seconds() == 3600
    manager.async_update = AsyncMock()
    await interval.call_args.args[1](None)
    manager.async_update.assert_awaited_once()
    hass.bus.async_listen_once.call_args.args[1](None)
    cancel.assert_called_once()
