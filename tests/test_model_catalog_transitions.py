"""Compatibility invariants for hot model-catalogue transitions."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DOMAIN,
)


class MemoryStore:
    """Small in-memory replacement for Home Assistant Store."""

    def __init__(self) -> None:
        self.saved = None

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, value):
        self.saved = deepcopy(value)


@pytest.fixture(autouse=True)
def reset_catalog():
    """Do not let module-global catalogue state leak between tests."""
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


def _expanded_candidate() -> dict:
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"].append("minimal")
    return candidate


def _transport(monkeypatch, raw: bytes, *, etag: str = '"catalog"') -> Mock:
    async def chunks(_size):
        yield raw

    response = SimpleNamespace(
        status=200,
        headers={"ETag": etag},
        content=SimpleNamespace(iter_chunked=chunks),
    )
    context = AsyncMock()
    context.__aenter__.return_value = response
    get = Mock(return_value=context)
    monkeypatch.setattr(
        runtime, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )
    return get


def test_hot_transition_cannot_remove_previously_valid_reasoning_choice() -> None:
    current = _expanded_candidate()
    candidate = deepcopy(current)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"].remove("minimal")

    with pytest.raises(ValueError, match="cannot remove reasoning effort choices"):
        data.validate_catalog_transition(current, candidate)


def test_hot_transition_cannot_remove_reasoning_capability() -> None:
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["supported"] = False
    model["reasoning"]["efforts"] = []
    model["reasoning"]["openai_default"] = None
    model["recommended_profile"]["reasoning_effort"] = None

    with pytest.raises(ValueError, match="cannot remove reasoning support"):
        data.validate_catalog_transition(None, candidate)


def test_new_exact_model_does_not_inherit_unknown_model_capabilities() -> None:
    """An unknown ID has no permissive family fallback to preserve in v2."""
    assert data.model_metadata("private-model")["status"] == "unknown"
    assert data.model_metadata("private-model")["reasoning"]["efforts"] == []

    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = deepcopy(next(item for item in candidate["models"] if item["id"] == "gpt-4.1"))
    model.update(id="private-model", display_name="private-model")
    candidate["models"].append(model)

    data.validate_catalog_transition(None, candidate)


async def test_manager_rejects_narrowing_and_keeps_last_good_catalog(
    hass, monkeypatch
) -> None:
    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    current = _expanded_candidate()
    _transport(monkeypatch, json.dumps(current).encode(), etag='"v2"')
    assert (await manager.async_update(force=True))["last_error"] is None

    candidate = deepcopy(current)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"].remove("minimal")
    _transport(monkeypatch, json.dumps(candidate).encode(), etag='"v3"')

    result = await manager.async_update(force=True)
    assert result["last_error"]
    assert manager.catalog == current
    assert manager.store.saved["catalog"] == current
    assert data.model_metadata("gpt-5.6")["reasoning"]["efforts"][-1] == "minimal"


async def test_restart_rejects_stored_override_that_narrows_bundled_choices(hass) -> None:
    candidate = deepcopy(data.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    model = next(item for item in candidate["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"] = ["low"]

    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.store.saved = {
        "catalog": candidate,
        "etag": '"bad"',
        "last_checked": 0,
    }
    await manager.async_load()

    assert manager.catalog is None
    assert manager.status()["source"] == "bundled"
    assert manager.status()["last_error"]
    assert data.model_metadata("gpt-5.6")["reasoning"]["efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


async def test_reset_is_blocked_when_saved_agent_uses_download_only_choice(hass) -> None:
    current = _expanded_candidate()
    subentry = SimpleNamespace(
        data={
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "minimal",
        },
        subentry_id="conversation-subentry",
        subentry_type="conversation",
    )
    entry = SimpleNamespace(
        entry_id="catalog-entry",
        subentries={subentry.subentry_id: subentry},
    )
    hass.config_entries.async_entries.return_value = [entry]

    manager = runtime.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.catalog = current
    manager.etag = '"v2"'
    manager.store.saved = {
        "catalog": current,
        "etag": manager.etag,
        "last_checked": 0,
    }
    data.activate_catalog(current)

    result = await manager.async_reset()

    hass.config_entries.async_entries.assert_called_once_with(DOMAIN)
    assert result["source"] == "downloaded"
    assert "saved configuration" in result["last_error"]
    assert manager.catalog == current
    assert manager.store.saved["catalog"] == current
    assert data.model_metadata("gpt-5.6")["reasoning"]["efforts"][-1] == "minimal"
