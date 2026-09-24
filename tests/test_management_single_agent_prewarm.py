"""The optional single-agent Management prewarm shares the normal read cache."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
)
from homeassistant.config_entries import ConfigEntryState


class _Entry:
    def __init__(self, entry_id: str, agents: int) -> None:
        self.entry_id = entry_id
        self.data = {"api_key": "test"}
        self.title = "Provider"
        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.subentries = {
            f"agent-{index}": SimpleNamespace(
                subentry_id=f"agent-{index}",
                subentry_type="conversation",
                title=f"Agent {index}",
                data=agent_config_defaults(),
            )
            for index in range(agents)
        }
        self.listeners = []
        self.unload_callbacks = []
        self.background = []

    def add_update_listener(self, _listener):
        return lambda: None

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def async_on_state_change(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def async_create_background_task(self, _hass, coroutine, name, *, eager_start):
        self.background.append((coroutine, name, eager_start))

    def mark_loaded(self):
        self.state = ConfigEntryState.LOADED
        for callback in list(self.listeners):
            callback()


def _hass(*entries):
    class ConfigEntries:
        async_forward_entry_setups = AsyncMock()
        async_unload_platforms = AsyncMock(return_value=True)

        def async_entries(self, domain):
            assert domain == integration.DOMAIN
            return list(entries)

    return SimpleNamespace(
        data={},
        config_entries=ConfigEntries(),
        async_add_executor_job=lambda callback, *args: asyncio.to_thread(
            callback, *args
        ),
    )


def _stub_setup(monkeypatch):
    monkeypatch.setattr(
        integration, "get_authenticated_client", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(integration, "DebugOpenAIClientProxy", lambda client: client)
    monkeypatch.setattr(
        integration,
        "PerformanceOpenAIClientProxy",
        lambda client, **_kwargs: client,
    )
    monkeypatch.setattr(integration, "async_setup_templates", AsyncMock())
    monkeypatch.setattr(integration, "async_unload_templates", AsyncMock())


@pytest.mark.parametrize("agent_count", [0, 1, 2])
async def test_setup_schedules_only_the_sole_agent_after_loaded(
    monkeypatch, agent_count
) -> None:
    _stub_setup(monkeypatch)
    entry = _Entry("provider", agent_count)
    hass = _hass(entry)
    warm = AsyncMock()
    monkeypatch.setattr(integration, "async_prewarm_persisted_config_projection", warm)

    assert await integration.async_setup_entry(hass, entry) is True
    assert entry.background == []  # Setup did not execute or await the warm work.
    warm.assert_not_called()
    entry.mark_loaded()
    assert len(entry.background) == (1 if agent_count == 1 else 0)
    if agent_count == 1:
        coroutine, _name, eager_start = entry.background.pop()
        assert eager_start is False
        await coroutine
        warm.assert_awaited_once_with(hass, entry, entry.subentries["agent-0"])
    else:
        warm.assert_not_called()


async def test_two_agents_across_provider_entries_are_not_guessed(monkeypatch) -> None:
    _stub_setup(monkeypatch)
    first, second = _Entry("first", 1), _Entry("second", 1)
    hass = _hass(first, second)
    assert await integration.async_setup_entry(hass, first) is True
    first.mark_loaded()
    assert first.background == []


async def test_failed_warm_task_does_not_affect_setup(monkeypatch, caplog) -> None:
    _stub_setup(monkeypatch)
    entry = _Entry("provider", 1)
    hass = _hass(entry)
    monkeypatch.setattr(
        integration,
        "async_prewarm_persisted_config_projection",
        AsyncMock(side_effect=RuntimeError("optional warm failed")),
    )
    assert await integration.async_setup_entry(hass, entry) is True
    entry.mark_loaded()
    await entry.background.pop()[0]
    assert not [record for record in caplog.records if record.levelno >= 30]


async def test_cancelled_warm_and_unload_do_not_resurrect_projection(
    monkeypatch,
) -> None:
    _stub_setup(monkeypatch)
    entry = _Entry("provider", 1)
    hass = _hass(entry)
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    assert await integration.async_setup_entry(hass, entry) is True
    entry.mark_loaded()
    coroutine = entry.background.pop()[0]
    coroutine.close()  # Entry-owned background task cancelled before it ran.
    for callback in reversed(entry.unload_callbacks):
        callback()
    entry.unload_callbacks.clear()
    entry.state = ConfigEntryState.NOT_LOADED
    assert await integration.async_unload_entry(hass, entry) is True
    assert repair._persisted_projections == OrderedDict()
    assert entry.listeners == []
    assert await integration.async_setup_entry(hass, entry) is True
    entry.mark_loaded()
    assert len(entry.background) == 1
    await entry.background.pop()[0]
    subentry = entry.subentries["agent-0"]
    assert repair.persisted_config_projection(subentry).snapshot is not None
    for callback in reversed(entry.unload_callbacks):
        callback()
    entry.state = ConfigEntryState.NOT_LOADED
    assert await integration.async_unload_entry(hass, entry) is True
    assert repair._persisted_projections == OrderedDict()


def _read_request(hass, entry):
    subentry = next(iter(entry.subentries.values()))
    return management_ui._ManagementRequest(
        hass,
        "admin",
        True,
        {"section": "configuration", "action": "get"},
        entry.entry_id,
        subentry.subentry_id,
        entry,
        subentry,
    )


async def test_valid_prewarm_builds_the_snapshot_reused_by_first_read(
    monkeypatch,
) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    original_data = deepcopy(subentry.data)
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    snapshot = Mock(wraps=repair.agent_config_snapshot)
    monkeypatch.setattr(repair, "agent_config_snapshot", snapshot)
    original_executor = hass.async_add_executor_job

    async def counted_executor(callback, *args):
        return await original_executor(callback, *args)

    executor = AsyncMock(side_effect=counted_executor)
    hass.async_add_executor_job = executor

    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    warmed = repair.persisted_config_projection(subentry)
    assert warmed.snapshot is not None
    assert snapshot.call_count == 1
    executor.assert_awaited_once()
    result = await management_ui.async_configuration_command(_read_request(hass, entry))
    assert result["_performance"]["snapshot_cache_hit"] is True
    assert snapshot.call_count == 1
    executor.assert_awaited_once()
    assert result["config"] == warmed.snapshot
    assert result["config"] == agent_config_snapshot(dict(subentry.data))
    assert subentry.data == original_data


async def test_repairable_prewarm_reuses_quarantine_on_first_read(
    monkeypatch,
) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    tools = yaml.safe_load(subentry.data["functions"])
    invalid = deepcopy(tools[0])
    invalid["spec"]["name"] = "unavailable_native"
    invalid["function"] = {"type": "native", "name": "removed_native_implementation"}
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([*tools, invalid])}
    original_data = deepcopy(subentry.data)
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    repair._cached_isolated_function_tools.cache_clear()
    isolate = Mock(wraps=repair._isolate_function_tools_uncached)
    monkeypatch.setattr(repair, "_isolate_function_tools_uncached", isolate)

    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    assert isolate.call_count == 1
    result = await management_ui.async_configuration_command(_read_request(hass, entry))
    assert result["_performance"]["repair_state_cache_hit"] is True
    assert result["function_repair"]["invalid_count"] == 1
    assert isolate.call_count == 1
    assert subentry.data == original_data


async def test_function_mutation_after_prewarm_rebuilds_current_snapshot(
    monkeypatch,
) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    snapshot = Mock(wraps=repair.agent_config_snapshot)
    monkeypatch.setattr(repair, "agent_config_snapshot", snapshot)
    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    changed = yaml.safe_load(subentry.data["functions"])
    changed[0]["spec"]["name"] = "changed_after_warm"
    subentry.data = {**subentry.data, "functions": yaml.safe_dump(changed)}

    result = await management_ui.async_configuration_command(_read_request(hass, entry))
    assert result["_performance"]["snapshot_cache_hit"] is False
    assert result["config"]["functions"][0]["spec"]["name"] == "changed_after_warm"
    assert snapshot.call_count == 2


async def test_replaced_data_during_executor_parse_cannot_publish_old_snapshot(
    monkeypatch,
) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_parse(callback, *args):
        entered.set()
        await release.wait()
        return callback(*args)

    hass.async_add_executor_job = delayed_parse
    pending = asyncio.create_task(
        repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    )
    await entered.wait()
    old_projection = repair.persisted_config_projection(subentry)
    subentry.data = {**subentry.data, "max_tokens": 777}
    release.set()
    await pending
    assert old_projection.snapshot is None
    current = repair.persisted_config_projection(subentry)
    assert current is not old_projection
    assert current.snapshot is None
    result = await management_ui.async_configuration_command(_read_request(hass, entry))
    assert result["config"]["max_tokens"] == 777
    assert result["_performance"]["snapshot_cache_hit"] is False


async def test_cancelled_parse_cannot_publish_snapshot(monkeypatch) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_parse(callback, *args):
        entered.set()
        await release.wait()
        return callback(*args)

    hass.async_add_executor_job = delayed_parse
    pending = asyncio.create_task(
        repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    )
    await entered.wait()
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert repair.persisted_config_projection(subentry).snapshot is None


async def test_nonisolatable_prewarm_keeps_conservative_read(monkeypatch) -> None:
    entry = _Entry("provider", 1)
    entry.mark_loaded()
    hass = _hass(entry)
    subentry = entry.subentries["agent-0"]
    subentry.data = {**subentry.data, "functions": "not a list"}
    original_data = deepcopy(subentry.data)
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())

    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    projection = repair.persisted_config_projection(subentry)
    assert projection.snapshot is None
    assert projection.repair_state is not None
    assert projection.repair_state.invalid == []
    result = await management_ui.async_configuration_command(_read_request(hass, entry))
    assert result["function_repair"]["isolatable"] is False
    assert subentry.data == original_data
