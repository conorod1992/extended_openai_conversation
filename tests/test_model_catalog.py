"""Catalogue parity, fail-safe refresh, persistence, and shared consumers."""

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
from custom_components.extended_openai_conversation_responses.agent_config import (
    model_capabilities,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_api_mode,
    get_model_config,
    get_reasoning_effort_options,
    get_token_param_for_model,
)
from custom_components.extended_openai_conversation_responses.performance import (
    _supports_explicit_cache,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    validate_rule,
)


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(data, "_active", data.BUNDLED_CATALOG)


def candidate():
    value = deepcopy(data.BUNDLED_CATALOG)
    value["catalog_version"] += 1
    model = next(item for item in value["models"] if item["id"] == "gpt-5.6")
    model["reasoning_efforts"] = ["low", "medium", "high", "xhigh"]
    model["parameters"]["supports_temperature"] = True
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


def transport(monkeypatch, raw, *, status=200, etag='"v2"', error=None):
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


def test_bundled_catalog_loads_and_is_descriptive_only():
    assert (
        data.parse_catalog(Path(data.__file__).with_suffix(".json").read_bytes())
        == data.BUNDLED_CATALOG
    )
    assert data.model_metadata("gpt-6-astra")["kind"] == "alias"
    with pytest.raises(ValueError):
        data.validate_catalog({**candidate(), "request_expression": "eval(...)"})


@pytest.mark.parametrize(
    ("model", "expected"),
    json.loads(
        (Path(__file__).parent / "fixtures/model_capability_parity.json").read_text()
    ).items(),
)
def test_legacy_capability_parity(model, expected):
    assert get_model_config(model) == expected["parameters"]
    assert get_reasoning_effort_options(model) == expected["reasoning_efforts"]
    assert (get_token_param_for_model(model) == "max_completion_tokens") == expected[
        "completion_token_limit"
    ]
    assert (get_api_mode("auto", model) == "chat_completions") == expected[
        "chat_reasoning_tools"
    ]
    assert _supports_explicit_cache(model) == expected["explicit_prompt_cache"]
    assert get_api_mode("responses", model) == "responses"
    assert get_api_mode("chat_completions", model) == "chat_completions"


def test_unknown_fallback_and_returned_values_are_isolated():
    config = get_model_config("private-model")
    config["supports_temperature"] = False
    efforts = get_reasoning_effort_options("private-model")
    efforts.clear()
    assert data.model_metadata("private-model") == data.BUNDLED_CATALOG["defaults"]


async def test_download_updates_consumers_and_persists_across_restart(
    manager, monkeypatch
):
    value = candidate()
    get = transport(monkeypatch, json.dumps(value).encode())
    result = await manager.async_update(force=True)
    assert result["source"] == "downloaded" and result["last_error"] is None
    assert get.call_args.kwargs == {"headers": {}, "allow_redirects": False}
    assert get_reasoning_effort_options("gpt-5.6") == ["low", "medium", "high", "xhigh"]
    assert model_capabilities("gpt-5.6")["supports_temperature"] is True
    rule = {
        "id": "route",
        "name": "Route",
        "enabled": True,
        "phrases": ["think"],
        "match_type": "starts_with",
        "action_type": "model_routing",
        "action": {
            "model": "gpt-5.6",
            "reasoning_effort": "xhigh",
            "scope": "request",
            "reset": False,
            "success_response": "Updated",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }
    assert validate_rule(rule)["action"]["reasoning_effort"] == "xhigh"
    snapshot = build_provider_request_snapshot(
        {
            "chat_model": "gpt-5.6",
            "reasoning_effort": "xhigh",
            "temperature": 0.3,
            "api_mode": "chat_completions",
        },
        {},
    )
    assert snapshot.api_kwargs["temperature"] == 0.3
    assert snapshot.api_kwargs["reasoning_effort"] == "xhigh"
    data.activate_catalog(None)
    restarted = runtime.ModelCatalogManager(manager.hass)
    restarted.store = manager.store
    await restarted.async_load()
    assert restarted.etag == '"v2"'
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "xhigh"


def test_partial_override_and_exact_snapshot_precedence():
    value = candidate()
    alias = next(item for item in value["models"] if item["id"] == "gpt-5.6")
    specific = {
        **deepcopy(alias),
        "id": "gpt-5.6-2026-09-01",
        "kind": "snapshot",
        "reasoning_efforts": ["high"],
    }
    value["models"] = [alias, specific]
    data.activate_catalog(value)
    assert get_reasoning_effort_options(specific["id"]) == ["high"]
    assert get_reasoning_effort_options("gpt-5.6-2026-09-02")[-1] == "xhigh"
    assert get_reasoning_effort_options("gpt-6-astra")[-1] == "max"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(schema_version=2),
        lambda v: v.update(schema_version=True),
        lambda v: v.update(catalog_version="next"),
        lambda v: v.update(models=[]),
        lambda v: v["models"].append(deepcopy(v["models"][0])),
        lambda v: v["models"][0].update(pattern=".*"),
        lambda v: v["models"][0].update(reasoning_efforts=["run code"]),
        lambda v: v["models"][0].update(kind="expression"),
        lambda v: v["models"][0]["parameters"].update(supports_top_p=1),
        lambda v: v["models"][0].update(deprecated="yes"),
    ],
)
async def test_invalid_download_retains_good_catalog(manager, monkeypatch, mutate):
    transport(monkeypatch, json.dumps(candidate()).encode())
    await manager.async_update(force=True)
    previous = deepcopy(manager.catalog)
    value = candidate()
    mutate(value)
    transport(monkeypatch, json.dumps(value).encode())
    assert (await manager.async_update(force=True))["last_error"]
    assert manager.catalog == previous
    assert manager.store.saved["catalog"] == previous
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "xhigh"


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b'{"schema_version":1,"schema_version":1}',
        b" " * (data.MAX_CATALOG_BYTES + 1),
    ],
    ids=["truncated", "array", "duplicate-key", "oversized"],
)
async def test_malformed_and_oversized_json_rejected(manager, monkeypatch, raw):
    transport(monkeypatch, raw)
    assert (await manager.async_update(force=True))["last_error"]
    assert manager.catalog is None
    assert get_reasoning_effort_options("gpt-5.6") == ["low", "medium", "high"]


@pytest.mark.parametrize(
    "failure", ["network", "http", "storage", "old", "same-version"]
)
async def test_failed_update_never_replaces_current_data(manager, monkeypatch, failure):
    transport(monkeypatch, json.dumps(candidate()).encode())
    await manager.async_update(force=True)
    previous = deepcopy(manager.catalog)
    value = candidate()
    value["catalog_version"] = 3
    value["models"][0]["display_name"] = "New label"
    if failure == "old":
        value["catalog_version"] = 1
    elif failure == "same-version":
        value["catalog_version"] = 2
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
    assert manager.store.saved["catalog"] == previous


async def test_daily_cadence_etag_and_manual_reset(manager, monkeypatch):
    now = 1000000.0
    monkeypatch.setattr(runtime.time, "time", lambda: now)
    get = transport(monkeypatch, json.dumps(candidate()).encode())
    await manager.async_update()
    now += runtime.UPDATE_INTERVAL - 1
    await manager.async_update()
    assert get.call_count == 1
    now += 1
    get = transport(monkeypatch, b"", status=304)
    await manager.async_update()
    assert get.call_args.kwargs["headers"] == {"If-None-Match": '"v2"'}
    assert manager.status()["last_error"] is None
    assert manager.store.saved["catalog"]["catalog_version"] == 2
    await manager.async_update(force=True)
    assert get.call_count == 2
    assert (await manager.async_reset())["source"] == "bundled"
    assert manager.etag is None and manager.store.saved["catalog"] is None
    await manager.async_update()
    assert get.call_count == 2
    assert get_reasoning_effort_options("gpt-5.6") == ["low", "medium", "high"]
    restarted = runtime.ModelCatalogManager(manager.hass)
    restarted.store = manager.store
    await restarted.async_load()
    assert restarted.status()["source"] == "bundled"
    assert restarted.last_checked == now


async def test_failed_attempt_cadence_survives_restart(manager, monkeypatch):
    get = transport(monkeypatch, b"", error=OSError("offline"))
    await manager.async_update()
    restarted = runtime.ModelCatalogManager(manager.hass)
    restarted.store = manager.store
    await restarted.async_load()
    await restarted.async_update()
    assert get.call_count == 1


async def test_corrupt_storage_loads_bundled_and_reset_save_failure_keeps_download(
    manager, monkeypatch
):
    manager.store.saved = {"catalog": {"schema_version": 99}}
    await manager.async_load()
    assert manager.status()["source"] == "bundled"
    transport(monkeypatch, json.dumps(candidate()).encode())
    await manager.async_update(force=True)
    manager.store.fail = True
    assert (await manager.async_reset())["source"] == "downloaded"
    assert get_reasoning_effort_options("gpt-5.6")[-1] == "xhigh"


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
    # A not-yet-durable catalogue must never reach synchronous request helpers.
    assert get_reasoning_effort_options("gpt-5.6") == ["low", "medium", "high"]
    reset = asyncio.create_task(manager.async_reset())
    release.set()
    await asyncio.gather(update, reset)
    assert manager.catalog is None and manager.store.saved["catalog"] is None


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
