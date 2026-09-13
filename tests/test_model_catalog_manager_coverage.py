"""Residual behavioral coverage for the model catalog manager."""

from __future__ import annotations

from copy import deepcopy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)


class MemoryStore:
    """Small in-memory replacement for Home Assistant storage."""

    def __init__(self, saved=None) -> None:
        self.saved = deepcopy(saved)
        self.fail_save = False

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, value) -> None:
        if self.fail_save:
            raise OSError("disk full")
        self.saved = deepcopy(value)


def _manager(hass) -> runtime.ModelCatalogManager:
    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    return manager


def _candidate() -> dict:
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    candidate["models"][0]["display_name"] = "Downloaded catalogue"
    return candidate


def _subentry(*, model="gpt-5.6", effort=None):
    values = {CONF_CHAT_MODEL: model}
    if effort is not None:
        values[CONF_REASONING_EFFORT] = effort
    return SimpleNamespace(
        subentry_type="conversation",
        subentry_id="agent",
        data=values,
    )


def _entry(subentry) -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry", subentries={"agent": subentry})


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(data, "_active", data.BUNDLED_CATALOG)


@pytest.mark.parametrize(
    "saved",
    [
        {"catalog": None, "etag": None, "last_checked": True},
        {"catalog": None, "etag": None, "last_checked": time.time() + 3600},
        {"catalog": None, "etag": "bad\netag", "last_checked": 0},
        {"catalog": None, "etag": "x" * 257, "last_checked": 0},
    ],
    ids=["boolean-check-time", "future-check-time", "newline-etag", "oversized-etag"],
)
async def test_load_rejects_invalid_storage_metadata(hass, saved) -> None:
    manager = _manager(hass)
    manager.store = MemoryStore(saved)

    await manager.async_load()

    assert manager.catalog is None
    assert manager.last_error == "Stored model data could not be loaded; using bundled data."
    assert manager.status()["source"] == "bundled"


async def test_load_discards_download_older_than_bundled(hass, monkeypatch) -> None:
    manager = _manager(hass)
    old = deepcopy(data.BUNDLED_CATALOG)
    old["catalog_version"] -= 1
    manager.store = MemoryStore(
        {"catalog": old, "etag": '"stale"', "last_checked": 0}
    )
    monkeypatch.setattr(
        runtime,
        "validate_or_migrate_catalog",
        lambda value: (deepcopy(value), False),
    )

    await manager.async_load()

    assert manager.catalog is None
    assert manager.etag is None
    assert manager.last_error is None


async def test_migrated_load_survives_rewrite_failure(hass, monkeypatch) -> None:
    manager = _manager(hass)
    candidate = _candidate()
    manager.store = MemoryStore(
        {"catalog": {"legacy": True}, "etag": None, "last_checked": 0}
    )
    manager.store.fail_save = True
    monkeypatch.setattr(
        runtime,
        "validate_or_migrate_catalog",
        lambda _value: (deepcopy(candidate), True),
    )

    await manager.async_load()

    assert manager.catalog == candidate
    assert manager.last_error is None
    assert data.model_metadata(candidate["models"][0]["id"])["display_name"] == (
        "Downloaded catalogue"
    )


async def test_failed_update_record_survives_storage_failure(hass) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    manager.etag = '"good"'
    manager.store.fail_save = True

    await manager._record_failed_update(123.0, transient=False)

    assert manager.catalog is not None
    assert manager.etag == '"good"'
    assert manager.last_error == "Model data update failed; the current catalogue was kept."


async def test_unsolicited_not_modified_is_rejected(hass, monkeypatch) -> None:
    manager = _manager(hass)
    response = SimpleNamespace(status=304, headers={}, content=None)
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = SimpleNamespace(get=lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runtime, "async_get_clientsession", lambda _hass: session)

    status = await manager.async_update(force=True)

    assert status["source"] == "bundled"
    assert status["last_error"]


@pytest.mark.parametrize("status_code", [429, 503, 400])
async def test_http_failures_keep_current_catalog(hass, monkeypatch, status_code) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    previous = deepcopy(manager.catalog)
    response = SimpleNamespace(status=status_code, headers={}, content=None)
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = SimpleNamespace(get=lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runtime, "async_get_clientsession", lambda _hass: session)

    status = await manager.async_update(force=True)

    assert status["last_error"]
    assert manager.catalog == previous


async def test_direct_saved_reasoning_can_block_reset(hass, monkeypatch) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    hass.config_entries.async_entries.return_value = [
        _entry(_subentry(effort="not-a-bundled-effort"))
    ]
    rules = SimpleNamespace(snapshot=lambda: {"rules": []})
    monkeypatch.setattr(runtime, "async_get_request_rules", AsyncMock(return_value=rules))

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is True


async def test_irrelevant_subentries_do_not_load_request_rules(hass, monkeypatch) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(
            entry_id="entry",
            subentries={
                "task": SimpleNamespace(
                    subentry_type="ai_task", subentry_id="task", data={}
                )
            },
        )
    ]
    get_rules = AsyncMock()
    monkeypatch.setattr(runtime, "async_get_request_rules", get_rules)

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False
    get_rules.assert_not_awaited()


@pytest.mark.parametrize(
    "action",
    [
        {"model": "gpt-5.6", "reasoning_effort": "not-a-bundled-effort"},
        {"reasoning_effort": "not-a-bundled-effort"},
    ],
    ids=["fixed-model", "all-models"],
)
async def test_request_rule_reasoning_can_block_reset(hass, monkeypatch, action) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    hass.config_entries.async_entries.return_value = [_entry(_subentry())]
    rules = SimpleNamespace(
        snapshot=lambda: {
            "rules": [{"action_type": "model_routing", "action": action}]
        }
    )
    monkeypatch.setattr(runtime, "async_get_request_rules", AsyncMock(return_value=rules))

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is True


async def test_dynamic_and_reset_rules_do_not_block_catalog_reset(
    hass, monkeypatch
) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    hass.config_entries.async_entries.return_value = [_entry(_subentry())]
    rules = SimpleNamespace(
        snapshot=lambda: {
            "rules": [
                {"action_type": "other", "action": {}},
                {"action_type": "model_routing", "action": {"reset": True}},
                {
                    "action_type": "model_routing",
                    "action": {"reasoning_effort": "{effort}"},
                },
                {
                    "action_type": "model_routing",
                    "action": {
                        "model": "{model}",
                        "reasoning_effort": "medium",
                    },
                },
            ]
        }
    )
    monkeypatch.setattr(runtime, "async_get_request_rules", AsyncMock(return_value=rules))

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False


async def test_reset_block_preserves_downloaded_catalog(hass, monkeypatch) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    original = deepcopy(manager.catalog)
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=True),
    )

    status = await manager.async_reset()

    assert status["source"] == "downloaded"
    assert "blocked" in status["last_error"]
    assert manager.catalog == original


async def test_reset_save_failure_preserves_downloaded_catalog(hass, monkeypatch) -> None:
    manager = _manager(hass)
    manager.catalog = _candidate()
    manager.etag = '"downloaded"'
    original = deepcopy(manager.catalog)
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(manager, "_save", AsyncMock(side_effect=OSError("disk full")))

    status = await manager.async_reset()

    assert status["source"] == "downloaded"
    assert "reset failed" in status["last_error"]
    assert manager.catalog == original
    assert manager.etag == '"downloaded"'


async def test_no_downloaded_catalog_never_blocks_reset(hass) -> None:
    manager = _manager(hass)

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False
