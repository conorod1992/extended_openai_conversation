"""Residual coverage for model catalogue lifecycle and websocket behavior."""

from __future__ import annotations

from copy import deepcopy
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as catalog,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)


def _candidate(*, increment: int = 1) -> dict:
    value = deepcopy(catalog.BUNDLED_CATALOG)
    value["catalog_version"] += increment
    value["models"][0]["display_name"] = f"Updated {increment}"
    return value


class _ResponseContext:
    def __init__(self, response) -> None:
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


def _install_transport(monkeypatch, payload: dict, *, etag: str | None = '"next"') -> Mock:
    raw = json.dumps(payload).encode()

    async def chunks(_size: int):
        yield raw

    response = SimpleNamespace(
        status=200,
        headers={} if etag is None else {"ETag": etag},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    get = Mock(return_value=_ResponseContext(response))
    monkeypatch.setattr(
        runtime,
        "async_get_clientsession",
        lambda _hass: SimpleNamespace(get=get),
    )
    return get


def _websocket_handler():
    """Return the undecorated handler when HA decorators expose wrapped callables."""
    return inspect.unwrap(runtime.websocket_catalog)


@pytest.mark.asyncio
async def test_initialize_accepts_stored_metadata_without_catalog(hass, monkeypatch) -> None:
    manager = runtime.ModelCatalogManager(hass)
    manager.store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"catalog": None, "etag": None, "last_checked": 0}
        )
    )
    validate_transition = Mock()
    activate = Mock()
    monkeypatch.setattr(runtime, "validate_catalog_transition", validate_transition)
    monkeypatch.setattr(runtime, "activate_catalog", activate)

    await manager.async_load()

    assert manager.catalog is None
    assert manager.etag is None
    assert manager.last_checked == 0
    assert manager.last_error is None
    validate_transition.assert_not_called()
    activate.assert_called_once_with(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("etag", ["x" * 257, '"bad\nvalue"', '"bad\rvalue"'])
async def test_update_rejects_invalid_response_etag(hass, monkeypatch, etag: str) -> None:
    manager = runtime.ModelCatalogManager(hass)
    _install_transport(monkeypatch, _candidate(), etag=etag)
    save = AsyncMock()
    activate = Mock()
    monkeypatch.setattr(manager, "_save", save)
    monkeypatch.setattr(runtime, "activate_catalog", activate)

    result = await manager.async_update(force=True)

    assert result["last_error"] == "Model data update failed; the current catalogue was kept."
    assert manager.catalog is None
    assert manager.etag is None
    activate.assert_not_called()
    assert save.await_count == 1
    saved_catalog, saved_etag, _checked = save.await_args.args
    assert saved_catalog is None
    assert saved_etag is None


@pytest.mark.asyncio
async def test_update_rejects_catalogue_version_rollback(hass, monkeypatch) -> None:
    manager = runtime.ModelCatalogManager(hass)
    current = _candidate(increment=2)
    manager.catalog = current
    manager.etag = '"current"'
    _install_transport(monkeypatch, _candidate(increment=1), etag='"older"')
    save = AsyncMock()
    activate = Mock()
    monkeypatch.setattr(manager, "_save", save)
    monkeypatch.setattr(runtime, "activate_catalog", activate)

    result = await manager.async_update(force=True)

    assert result["last_error"] == "Model data update failed; the current catalogue was kept."
    assert manager.catalog == current
    assert manager.etag == '"current"'
    activate.assert_not_called()
    assert save.await_count == 1
    saved_catalog, saved_etag, _checked = save.await_args.args
    assert saved_catalog == current
    assert saved_etag == '"current"'


@pytest.mark.asyncio
async def test_valid_model_routing_rule_continues_scanning(hass, monkeypatch) -> None:
    manager = runtime.ModelCatalogManager(hass)
    manager.catalog = _candidate()
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data={CONF_CHAT_MODEL: "gpt-5.6", CONF_REASONING_EFFORT: "medium"},
    )
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.config_entries.async_entries.return_value = [entry]
    rules = SimpleNamespace(
        snapshot=lambda: {
            "rules": [
                {
                    "action_type": "model_routing",
                    "action": {"model": "gpt-5.6", "reasoning_effort": "medium"},
                },
                {"action_type": "other", "action": {}},
            ]
        }
    )
    get_rules = AsyncMock(return_value=rules)
    monkeypatch.setattr(runtime, "async_get_request_rules", get_rules)

    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False
    get_rules.assert_awaited_once_with(hass, "entry", "agent")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["lookup", "reset", "update"])
async def test_websocket_actions_return_complete_catalog_payload(
    hass, monkeypatch, action: str
) -> None:
    status = {
        "source": "downloaded",
        "catalog_version": 7,
        "schema_version": 2,
        "last_checked": 123.0,
        "last_error": None,
    }
    manager = SimpleNamespace(
        catalog={"catalog_version": 7},
        status=Mock(return_value=status),
        async_update=AsyncMock(return_value=status),
        async_reset=AsyncMock(return_value=status),
    )
    hass.data[runtime.DATA_MANAGER] = manager
    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
    metadata = {"id": "gpt-test", "reasoning": {"efforts": ["low", "high"]}}
    capabilities = {"responses": True}
    picker = [{"id": "gpt-test"}]
    monkeypatch.setattr(runtime, "model_metadata", Mock(return_value=metadata))
    monkeypatch.setattr(
        runtime, "compatibility_capabilities", Mock(return_value=capabilities)
    )
    monkeypatch.setattr(runtime, "catalog_picker_models", Mock(return_value=picker))

    await _websocket_handler()(
        hass,
        connection,
        {"id": 42, "action": action, "model": "gpt-test"},
    )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once_with(
        42,
        {
            **status,
            "model_capabilities": capabilities,
            "model_metadata": metadata,
            "catalog_models": picker,
            "reasoning_effort_options": ["low", "high"],
        },
    )
    if action == "lookup":
        manager.async_update.assert_not_awaited()
        manager.async_reset.assert_not_awaited()
    elif action == "reset":
        manager.async_reset.assert_awaited_once_with()
        manager.async_update.assert_not_awaited()
    else:
        manager.async_update.assert_awaited_once_with(force=True)
        manager.async_reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_update_failure_sends_error_without_metadata_work(
    hass, monkeypatch
) -> None:
    failed_status = {
        "source": "bundled",
        "catalog_version": 2,
        "schema_version": 2,
        "last_checked": 123.0,
        "last_error": "refresh failed",
    }
    manager = SimpleNamespace(
        catalog=None,
        status=Mock(),
        async_update=AsyncMock(return_value=failed_status),
        async_reset=AsyncMock(),
    )
    hass.data[runtime.DATA_MANAGER] = manager
    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
    monkeypatch.setattr(
        runtime,
        "model_metadata",
        lambda *_args: pytest.fail("metadata should not be evaluated after update failure"),
    )
    monkeypatch.setattr(
        runtime,
        "compatibility_capabilities",
        lambda *_args: pytest.fail("capabilities should not be evaluated after update failure"),
    )
    monkeypatch.setattr(
        runtime,
        "catalog_picker_models",
        lambda *_args: pytest.fail("picker should not be evaluated after update failure"),
    )

    await _websocket_handler()(
        hass,
        connection,
        {"id": 99, "action": "update", "model": "gpt-test"},
    )

    manager.async_update.assert_awaited_once_with(force=True)
    connection.send_error.assert_called_once_with(
        99, "model_catalog_update_failed", "refresh failed"
    )
    connection.send_result.assert_not_called()
    manager.status.assert_not_called()
