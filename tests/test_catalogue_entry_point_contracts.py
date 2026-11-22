"""Both catalogue writers protect the same persisted request guarantees."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_catalog as data,
    model_catalog_manager as runtime,
)
from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
    ModelCatalogManager,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    PROVIDER_RESTORE_OWNERS,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_model_catalog import MemoryStore as RuleStore
from tests.test_model_catalog_transitions import MemoryStore


@pytest.fixture(autouse=True)
def isolate_catalogue():
    data.activate_catalog(None)
    yield
    data.activate_catalog(None)


@pytest.mark.parametrize("route", ["apply", "reset"])
@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
@pytest.mark.parametrize("valid", [False, True])
async def test_catalogue_writers_share_saved_request_atomicity(
    hass, route, kind, valid
):
    expanded = deepcopy(data.BUNDLED_CATALOG)
    expanded["catalog_version"] += 1
    model = next(item for item in expanded["models"] if item["id"] == "gpt-4o")
    model["limits"]["max_output_tokens"] = 32768
    data.activate_catalog(expanded)
    manager = ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.catalog = expanded
    candidate = deepcopy(expanded)
    candidate["catalog_version"] = expanded["catalog_version"] + 1
    manager.available_catalog = candidate
    child = SimpleNamespace(
        subentry_type=kind,
        subentry_id="child",
        title="Contract agent",
        data={
            "chat_model": "gpt-4o",
            "max_tokens": 500 if valid else (999999 if route == "apply" else 20000),
            "max_function_calls_per_conversation": 0,
            "functions": [],
        },
    )
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"child": child})
    ]
    before = deepcopy(child.data)
    result = await (
        manager.async_apply_update() if route == "apply" else manager.async_reset()
    )
    assert child.data == before
    assert manager.store.save_calls == int(valid)
    if valid:
        assert not result["last_error"]
        assert manager.catalog == (candidate if route == "apply" else None)
    else:
        assert result["last_error"]
        assert manager.catalog == expanded
        assert manager.available_catalog == candidate
        assert data.model_metadata("gpt-4o")["limits"]["max_output_tokens"] == 32768


@pytest.mark.parametrize("kind", ["conversation", "ai_task_data"])
@pytest.mark.parametrize("search", [False, True])
async def test_provider_switch_validates_children_before_publishing(
    hass, monkeypatch, kind, search
):
    from custom_components.extended_openai_conversation_responses import services
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from homeassistant.config_entries import ConfigEntryState
    from tests.test_service_handlers import _call, _handlers

    child = SimpleNamespace(
        subentry_type=kind,
        subentry_id="child",
        title="Contract child",
        data={
            "chat_model": "gpt-5.6",
            "api_mode": "responses",
            "web_search": search,
            "functions": [],
            "max_function_calls_per_conversation": 0,
        },
    )
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        disabled_by=None,
        entry_id="entry",
        domain=DOMAIN,
        data={"api_key": "old"},
        subentries={"child": child},
    )
    hass.config_entries.async_get_entry.return_value = entry
    authenticate = AsyncMock()
    monkeypatch.setattr(services, "get_authenticated_client", authenticate)
    handlers = await _handlers(hass)
    call = _call({"config_entry": "entry", "base_url": "https://custom.example/v1"})
    if search:
        with pytest.raises(HomeAssistantError, match="incompatible with agent"):
            await handlers["change_config"](call)
        hass.config_entries.async_update_entry.assert_not_called()
    else:
        await handlers["change_config"](call)
        hass.config_entries.async_update_entry.assert_called_once()
    authenticate.assert_awaited_once()
    assert entry.data == {"api_key": "old"}


@pytest.fixture
def catalogue_publication(hass):
    expanded = deepcopy(data.BUNDLED_CATALOG)
    expanded["catalog_version"] += 1
    model = next(item for item in expanded["models"] if item["id"] == "gpt-4o")
    model["limits"]["max_output_tokens"] = 32768
    candidate = deepcopy(expanded)
    candidate["catalog_version"] += 1
    data.activate_catalog(expanded)
    manager = ModelCatalogManager(hass)
    manager.catalog = expanded
    manager.available_catalog = candidate
    manager.store = MemoryStore({"catalog": expanded, "available_catalog": candidate})
    return manager, expanded, candidate


def _catalogue_assistant(identifier):
    return SimpleNamespace(
        subentry_id=identifier,
        subentry_type="conversation",
        title=f"Assistant {identifier}",
        data={"chat_model": "gpt-4o", "api_mode": "responses", "max_tokens": 500},
    )


async def _write_catalogue(manager, route):
    if route == "apply":
        return await manager.async_apply_update()
    return await manager.async_reset()


@pytest.mark.parametrize("route", ["apply", "reset"])
@pytest.mark.parametrize(
    "change",
    ["settings", "settings_aba", "rules", "rules_owner", "add", "remove", "provider"],
)
async def test_catalogue_writers_reject_stale_discovery(
    hass, monkeypatch, catalogue_publication, route, change
):
    manager, expanded, candidate = catalogue_publication
    first = _catalogue_assistant("first")
    later = _catalogue_assistant("later")
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        subentries={first.subentry_id: first, later.subentry_id: later},
    )
    hass.config_entries.async_entries.return_value = [entry]
    first_rules, later_rules = RequestRules(RuleStore()), RequestRules(RuleStore())
    await first_rules.async_initialize()
    await later_rules.async_initialize()
    hass.data[runtime._RULE_MANAGERS] = {
        ("entry", first.subentry_id): first_rules,
        ("entry", later.subentry_id): later_rules,
    }
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def get_rules(_hass, _entry_id, subentry_id):
        if subentry_id == first.subentry_id:
            return first_rules
        blocked.set()
        await release.wait()
        return later_rules

    monkeypatch.setattr(runtime, "async_get_request_rules", get_rules)
    before = deepcopy(manager.store.saved)
    generation = data.catalog_generation()
    task = asyncio.create_task(_write_catalogue(manager, route))
    try:
        await asyncio.wait_for(blocked.wait(), 5)
        if change == "settings":
            first.data["max_tokens"] = 501
        elif change == "settings_aba":
            old_settings = first.data
            first.data = {**old_settings, "max_tokens": 501}
            first.data = dict(old_settings)
        elif change == "rules":
            await first_rules.async_create(
                {
                    "name": "Switch to GPT-4o",
                    "phrases": ["switch model"],
                    "match_type": "equals",
                    "action_type": "model_routing",
                    "action": {"model": "gpt-4o", "scope": "conversation"},
                }
            )
        elif change == "rules_owner":
            replacement = RequestRules(RuleStore())
            await replacement.async_initialize()
            assert replacement.revision() == first_rules.revision()
            hass.data[runtime._RULE_MANAGERS][("entry", first.subentry_id)] = (
                replacement
            )
        elif change == "add":
            entry.subentries = {**entry.subentries, "new": _catalogue_assistant("new")}
        elif change == "remove":
            entry.subentries = {later.subentry_id: later}
        else:
            entry.data = {"api_provider": "openai", "api_key": "replacement"}
    finally:
        release.set()
        result = await task

    assert result["last_error"]
    assert manager.store.save_calls == 0
    assert manager.store.saved == before
    assert manager.catalog == expanded
    assert manager.available_catalog == candidate
    assert data.catalog_generation() == generation


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_freshness_is_checked_inside_the_owned_save(
    hass, monkeypatch, catalogue_publication, route
):
    manager, expanded, _ = catalogue_publication
    child = _catalogue_assistant("one")
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    ]
    rules = RequestRules(RuleStore())
    await rules.async_initialize()
    monkeypatch.setattr(
        runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    validate = manager._candidate_preserves_saved_requests

    async def validate_then_queue_edit(candidate, inventory):
        valid = await validate(candidate, inventory)
        asyncio.get_running_loop().call_soon(child.data.__setitem__, "max_tokens", 501)
        return valid

    monkeypatch.setattr(
        manager, "_candidate_preserves_saved_requests", validate_then_queue_edit
    )
    result = await _write_catalogue(manager, route)

    assert child.data["max_tokens"] == 501
    assert result["last_error"]
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_writers_settle_save_and_activation_on_cancellation(
    hass, monkeypatch, catalogue_publication, route
):
    manager, expanded, candidate = catalogue_publication
    committed = asyncio.Event()
    release = asyncio.Event()
    save = manager.store.async_save

    async def save_with_paused_acknowledgement(value):
        await save(value)
        committed.set()
        await release.wait()

    monkeypatch.setattr(manager.store, "async_save", save_with_paused_acknowledgement)
    task = asyncio.create_task(_write_catalogue(manager, route))
    try:
        await asyncio.wait_for(committed.wait(), 5)
        assert manager.catalog == expanded
        child = _catalogue_assistant("late")
        hass.config_entries.async_entries.return_value = [
            SimpleNamespace(entry_id="late-entry", data={}, subentries={"late": child})
        ]
        task.cancel()
        await asyncio.sleep(0)
        assert manager._lock.locked()
        assert not task.done()
        task.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert manager.catalog == manager.store.saved["catalog"]
    assert manager.catalog == (candidate if route == "apply" else None)
    assert data.active_catalog_version() == (
        candidate["catalog_version"]
        if route == "apply"
        else data.BUNDLED_CATALOG["catalog_version"]
    )
    assert manager.last_error is None


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_save_failure_during_cancellation_keeps_prior_generation(
    monkeypatch, catalogue_publication, route
):
    manager, expanded, candidate = catalogue_publication
    attempted, release = asyncio.Event(), asyncio.Event()
    before = deepcopy(manager.store.saved)
    generation = data.catalog_generation()

    async def fail_save(_value):
        attempted.set()
        await release.wait()
        raise OSError("disk full")

    monkeypatch.setattr(manager.store, "async_save", fail_save)
    task = asyncio.create_task(_write_catalogue(manager, route))
    try:
        await asyncio.wait_for(attempted.wait(), 5)
        task.cancel("writer cancelled")
        await asyncio.sleep(0)
        assert manager._lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError, match="writer cancelled"):
            await task

    assert manager.store.saved == before
    assert manager.catalog == expanded
    assert manager.available_catalog == candidate
    assert data.catalog_generation() == generation


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_cancelled_catalogue_discovery_can_be_retried(
    hass, monkeypatch, catalogue_publication, route
):
    manager, expanded, _ = catalogue_publication
    child = _catalogue_assistant("one")
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    ]
    rules = RequestRules(RuleStore())
    await rules.async_initialize()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def block_rules(*_args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(runtime, "async_get_request_rules", block_rules)
    task = asyncio.create_task(_write_catalogue(manager, route))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded

    monkeypatch.setattr(
        runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    result = await _write_catalogue(manager, route)
    assert result["last_error"] is None
    assert manager.store.save_calls == 1


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_publication_is_blocked_during_provider_restore(
    hass, monkeypatch, catalogue_publication, route
):
    manager, expanded, _ = catalogue_publication
    child = _catalogue_assistant("one")
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    ]
    rules = RequestRules(RuleStore())
    await rules.async_initialize()
    monkeypatch.setattr(
        runtime, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    hass.data[PROVIDER_RESTORE_OWNERS] = {"entry": "restore-transfer"}
    original = await _write_catalogue(manager, route)
    assert original["last_error"]
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded

    hass.data[PROVIDER_RESTORE_OWNERS].clear()
    restored = await _write_catalogue(manager, route)
    assert restored["last_error"] is None
    assert manager.store.save_calls == 1


async def test_reset_checks_downloaded_only_default_reasoning_before_rule_loading(
    hass, monkeypatch, catalogue_publication
):
    manager, expanded, _ = catalogue_publication
    model = next(item for item in expanded["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"].append("minimal")
    model["reasoning"]["by_api"]["responses"]["efforts"].append("minimal")
    data.activate_catalog(expanded)
    child = _catalogue_assistant("one")
    child.data.update(chat_model="gpt-5.6", reasoning_effort="minimal")
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    ]
    get_rules = AsyncMock(side_effect=OSError("rules unreadable"))
    monkeypatch.setattr(runtime, "async_get_request_rules", get_rules)

    result = await manager.async_reset()
    get_rules.assert_not_awaited()
    assert "reset was blocked" in result["last_error"]
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded

    child.data["reasoning_effort"] = "low"
    result = await manager.async_reset()
    get_rules.assert_awaited_once()
    assert "reset failed" in result["last_error"]
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded


async def test_catalogue_reset_never_validates_a_rules_write_that_rolls_back(
    hass, monkeypatch, catalogue_publication
):
    manager, expanded, _ = catalogue_publication
    model = next(item for item in expanded["models"] if item["id"] == "gpt-5.6")
    model["reasoning"]["efforts"].append("minimal")
    model["reasoning"]["by_api"]["responses"]["efforts"].append("minimal")
    data.activate_catalog(expanded)
    child = _catalogue_assistant("one")
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    ]
    store = RuleStore()
    rules = RequestRules(store)
    await rules.async_initialize()
    existing = await rules.async_create(
        {
            "name": "Route to GPT-5.6",
            "phrases": ["more reasoning"],
            "match_type": "equals",
            "action_type": "model_routing",
            "action": {
                "model": "gpt-5.6",
                "reasoning_effort": "minimal",
                "scope": "conversation",
            },
        }
    )
    initialized, finish_discovery = asyncio.Event(), asyncio.Event()
    deleting, finish_delete = asyncio.Event(), asyncio.Event()

    async def discover_rules(*_args):
        await rules.async_initialize()
        initialized.set()
        await finish_discovery.wait()
        return rules

    async def fail_delete(_value):
        deleting.set()
        await finish_delete.wait()
        raise OSError("disk full")

    monkeypatch.setattr(runtime, "async_get_request_rules", discover_rules)
    monkeypatch.setattr(store, "async_save", fail_delete)
    reset = asyncio.create_task(manager.async_reset())
    await asyncio.wait_for(initialized.wait(), 5)
    delete = asyncio.create_task(rules.async_delete(existing["id"]))
    try:
        await asyncio.wait_for(deleting.wait(), 5)
        assert rules.snapshot()["rules"] == []
        finish_discovery.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert manager.store.save_calls == 0
    finally:
        finish_discovery.set()
        finish_delete.set()
        with pytest.raises(OSError, match="disk full"):
            await delete
        result = await reset

    assert rules.snapshot()["rules"][0]["id"] == existing["id"]
    assert "reset was blocked" in result["last_error"]
    assert manager.store.save_calls == 0
    assert manager.catalog == expanded


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_writers_keep_a_commit_when_lifecycle_projection_fails(
    monkeypatch, catalogue_publication, route
):
    manager, _, candidate = catalogue_publication
    target = candidate if route == "apply" else None

    def project(_hass):
        assert manager.catalog == manager.store.saved["catalog"] == target
        raise RuntimeError("lifecycle projection failed")

    monkeypatch.setattr(runtime, "sync_all_model_lifecycles", project)

    result = await _write_catalogue(manager, route)

    assert result["last_error"] is None
    assert manager.catalog == manager.store.saved["catalog"] == target


@pytest.mark.parametrize("route", ["apply", "reset"])
async def test_catalogue_writers_reuse_one_rule_snapshot_and_activate_for_requests(
    hass, monkeypatch, catalogue_publication, route
):
    manager, _, candidate = catalogue_publication
    child = _catalogue_assistant("one")
    entry = SimpleNamespace(entry_id="entry", data={}, subentries={"one": child})
    hass.config_entries.async_entries.return_value = [entry]
    rules = RequestRules(RuleStore())
    await rules.async_initialize()
    get_rules = AsyncMock(return_value=rules)
    monkeypatch.setattr(runtime, "async_get_request_rules", get_rules)

    result = await _write_catalogue(manager, route)

    get_rules.assert_awaited_once_with(hass, "entry", "one")
    assert result["last_error"] is None
    assert manager.catalog == manager.store.saved["catalog"]
    request = build_provider_request_snapshot(child.data, entry.data)
    assert request.api_kwargs["model"] == "gpt-4o"
    assert request.api_kwargs["max_output_tokens"] == 500
    larger_request = {**child.data, "max_tokens": 20000}
    if route == "apply":
        assert manager.catalog == candidate
        assert (
            build_provider_request_snapshot(larger_request, entry.data).api_kwargs[
                "max_output_tokens"
            ]
            == 20000
        )
    else:
        assert manager.catalog is None
        with pytest.raises(HomeAssistantError):
            build_provider_request_snapshot(larger_request, entry.data)
