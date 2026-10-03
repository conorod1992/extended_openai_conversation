"""Narrow OS failure probes at HA's actual atomic fsync/replace seam."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import suppress
from datetime import timedelta
import errno
import os
from pathlib import Path

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    IntercomManager,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
)
from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
    ModelCatalogManager,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursManager,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION as RULES_VERSION,
    RequestRules,
    RequestRuleStore,
)
from custom_components.extended_openai_conversation_responses.restore_recovery import (
    _async_write_journal_verified,
    _journal_store,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    _temporary_memory_store,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, file as ha_file
from tests_stress.conftest import record


@pytest.mark.parametrize(
    "phase,boundary,timing",
    [
        ("schedule", boundary, timing)
        for boundary in ("before", "after")
        for timing in ("future", "overdue")
    ]
    + [("execution", boundary, "overdue") for boundary in ("before", "after")],
)
async def test_delayed_scheduler_same_process_recovers_after_write_and_read_failure(
    hass, monkeypatch, real_store_io, stress_trace, phase, boundary, timing
):
    from copy import deepcopy
    import json

    from custom_components.extended_openai_conversation_responses.agent_config import (
        normalize_agent_config,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_API_MODE,
        CONF_FUNCTION_TOOLS,
        DEFAULT_CONF_FUNCTION_TOOLS,
    )
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        async_setup_delayed_tools,
    )
    from homeassistant.components import conversation
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.core import Context
    from homeassistant.helpers import storage as ha_storage
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _install_wire,
    )

    owner = await hass.auth.async_create_user("Delayed recovery owner")
    delivered = asyncio.Event()
    effects = []

    async def turn_off(call):
        effects.append(call)
        delivered.set()

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.scheduler_recovery", "on")
    async_expose_entity(hass, conversation.DOMAIN, "light.scheduler_recovery", True)
    await hass.async_start()
    entry = _make_entry(
        "Delayed recovery",
        include_ai_task=False,
        conversation_options=normalize_agent_config(
            {
                CONF_API_MODE: "chat_completions",
                CONF_FUNCTION_TOOLS: [deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
            }
        ),
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    manager = await async_setup_delayed_tools(hass)
    path = Path(manager._store.path)

    async def schedule(call_id):
        arguments = {
            "delay": {"seconds": 2},
            "list": [
                {
                    "domain": "light",
                    "service": "turn_off",
                    "service_data": {"entity_id": ["light.scheduler_recovery"]},
                }
            ],
        }
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(call_id, "execute_services", arguments),
                _chat_sse_text("Finished scheduling attempt"),
            ],
        )
        result = await conversation.async_converse(
            hass=hass,
            text="Schedule the light",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.error_code is None
        return next(
            json.loads(message["content"])
            for message in wire.requests[-1]["body"]["messages"]
            if message.get("tool_call_id") == call_id
        )

    assert "Scheduled" in json.dumps(await schedule("initial"))
    assert len(manager._records) == 1
    original_worker = next(iter(manager._tasks.values()))
    assert not effects
    replace = atomicwrites.replace_atomic
    load = ha_storage.json_util.load_json
    write_attempted = asyncio.Event()

    def failed_replace(source, destination):
        if Path(destination) == path:
            if boundary == "after":
                replace(source, destination)
            hass.loop.call_soon_threadsafe(write_attempted.set)
            raise OSError(errno.EIO, "Controlled delayed write failure")
        return replace(source, destination)

    def failed_read(filename, *args, **kwargs):
        if Path(filename) == path:
            raise OSError(errno.EIO, "Controlled delayed readback failure")
        return load(filename, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", failed_replace)
        fault.setattr(ha_storage.json_util, "load_json", failed_read)
        if phase == "schedule":
            with pytest.raises(OSError):
                await schedule("unacknowledged")
        else:
            await asyncio.wait_for(write_attempted.wait(), 8)
            await asyncio.gather(original_worker, return_exceptions=True)
        assert not manager._setup_complete and not manager._started
        assert not manager._records and not manager._tasks and not effects
        with pytest.raises(OSError):
            await async_setup_delayed_tools(hass)
        if phase == "schedule" and timing == "overdue":
            await asyncio.sleep(2.1)
    disk = json.loads(path.read_text())["data"]["calls"]
    expected = len(disk) if phase == "schedule" else int(boundary == "before")
    recovered = await async_setup_delayed_tools(hass)
    assert recovered is manager
    assert manager._setup_complete and manager._started
    assert not manager._invalidated_tasks
    assert len(manager._tasks) == expected
    async with asyncio.timeout(8):
        while len(effects) < expected:
            delivered.clear()
            await delivered.wait()
        while manager._tasks:
            await asyncio.sleep(0.01)
    assert len(effects) == expected
    assert not manager._records
    assert json.loads(path.read_text())["data"]["calls"] == []
    # A discarded executing tombstone must not hide or replay the next healthy call.
    assert "Scheduled" in json.dumps(await schedule("healthy-retry"))
    async with asyncio.timeout(8):
        while len(effects) < expected + 1:
            delivered.clear()
            await delivered.wait()
        while manager._tasks:
            await asyncio.sleep(0.01)
    assert len(effects) == expected + 1
    assert json.loads(path.read_text())["data"]["calls"] == []
    _install_wire(monkeypatch, agent, [_chat_sse_text("Healthy foreground")])
    result = await conversation.async_converse(
        hass=hass,
        text="Are you healthy?",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert result.response.error_code is None
    record(
        stress_trace,
        "summary",
        delayed_same_manager_recoveries=1,
        delayed_compound_storage_faults=1,
        delayed_recovered_service_effects=expected,
        delayed_healthy_retry_effects=1,
        delayed_indeterminate_replay_rejections=int(
            phase == "execution" and boundary == "after"
        ),
    )


def _raise_os_error(number: int):
    def fail(*args, **kwargs):
        del args, kwargs
        raise OSError(number, "seeded private storage failure")

    return fail


def _files(path: str) -> set[str]:
    return {child.name for child in Path(path).parent.iterdir()}


@pytest.fixture
def real_store_io(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Undo pytest-HA's in-memory Store shim for these OS-boundary probes."""
    hass.config.config_dir = str(tmp_path)

    async def write_to_disk(store: Store, data: dict) -> None:
        # All selected EOAI stores serialize in the executor; retain HA's real
        # _write_data -> write_utf8_file_atomic -> fsync/replace path.
        await store.hass.async_add_executor_job(store._write_data, data)

    async def load_from_disk(store: Store):
        return await store._async_load_data()

    async def remove_from_disk(store: Store) -> None:
        store._manager.async_invalidate(store.key)
        store._async_cleanup_delay_listener()
        store._async_cleanup_final_write_listener()
        with suppress(FileNotFoundError):
            await store.hass.async_add_executor_job(os.unlink, store.path)

    # Restore the plugin's Store shim before its own fixture tears down. A
    # function-scoped monkeypatch teardown runs too late for its autospec.
    with monkeypatch.context() as scoped:
        scoped.setattr(Store, "_async_write_data", write_to_disk)
        scoped.setattr(Store, "_async_load", load_from_disk)
        scoped.setattr(Store, "async_remove", remove_from_disk)
        yield


async def test_knowledge_fsync_enospc_rolls_back_and_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    real_store_io: None,
) -> None:
    del real_store_io
    storage = HomeAssistantKnowledgeStorage(hass, "disk-entry", "disk-agent")
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    first = await library.async_create("Saved title", "Description", "Saved content")
    path = storage._store.path
    before_files = _files(path)
    before_bytes = Path(path).read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "_proper_fsync", _raise_os_error(errno.ENOSPC))
        with pytest.raises(OSError) as error:
            await library.async_update(first.source_id, content="Undurable content")
    assert error.value.errno == errno.ENOSPC
    assert (await library.async_get(first.source_id)).content == "Saved content"
    assert Path(path).read_bytes() == before_bytes
    assert _files(path) == before_files
    restarted = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, "disk-entry", "disk-agent")
    )
    await restarted.async_initialize()
    assert (await restarted.async_get(first.source_id)).content == "Saved content"
    await library.async_update(first.source_id, content="Recovered content")
    recovered = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, "disk-entry", "disk-agent")
    )
    await recovered.async_initialize()
    assert (await recovered.async_get(first.source_id)).content == "Recovered content"
    record(
        stress_trace,
        "os_storage_fault",
        store="knowledge",
        seam="fsync",
        errno="ENOSPC",
    )


async def test_request_rules_atomic_replace_erofs_rolls_back_and_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    real_store_io: None,
) -> None:
    del real_store_io
    key = "extended_openai_conversation.disk_fault_rules"
    store = RequestRuleStore(hass, RULES_VERSION, key)
    rules = RequestRules(store)
    await rules.async_initialize()
    await rules.async_set_groups(
        [{"id": "saved", "name": "Saved"}], expected_revision=rules.revision()
    )
    path = store.path
    before_files = _files(path)
    before_bytes = Path(path).read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.EROFS))
        with pytest.raises(OSError) as error:
            await rules.async_set_groups(
                [{"id": "lost", "name": "Lost"}], expected_revision=rules.revision()
            )
    assert error.value.errno == errno.EROFS
    assert rules.snapshot()["groups"] == [{"id": "saved", "name": "Saved"}]
    assert Path(path).read_bytes() == before_bytes
    assert _files(path) == before_files
    restarted = RequestRules(RequestRuleStore(hass, RULES_VERSION, key))
    await restarted.async_initialize()
    assert restarted.snapshot()["groups"] == [{"id": "saved", "name": "Saved"}]
    await rules.async_set_groups(
        [{"id": "recovered", "name": "Recovered"}], expected_revision=rules.revision()
    )
    recovered = RequestRules(RequestRuleStore(hass, RULES_VERSION, key))
    await recovered.async_initialize()
    assert recovered.snapshot()["groups"] == [{"id": "recovered", "name": "Recovered"}]
    record(
        stress_trace,
        "os_storage_fault",
        store="request_rules",
        seam="replace",
        errno="EROFS",
    )


async def test_restore_journal_replace_eacces_never_claims_commit(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    real_store_io: None,
) -> None:
    del real_store_io
    store = _journal_store(hass, "disk-entry", "disk-agent")
    saved = {"phase": "saved", "private_marker": "do-not-log-this"}
    assert await _async_write_journal_verified(store, saved)
    path = store.path
    before_files = _files(path)
    before_bytes = Path(path).read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.EACCES))
        assert not await _async_write_journal_verified(
            store, {"phase": "not-committed"}
        )
    assert Path(path).read_bytes() == before_bytes
    assert _files(path) == before_files
    assert await _journal_store(hass, "disk-entry", "disk-agent").async_load() == saved
    assert await _async_write_journal_verified(store, {"phase": "recovered"})
    assert await _journal_store(hass, "disk-entry", "disk-agent").async_load() == {
        "phase": "recovered"
    }
    record(
        stress_trace,
        "os_storage_fault",
        store="restore_journal",
        seam="replace",
        errno="EACCES",
    )


@pytest.mark.parametrize(
    "owner",
    (
        "persistent_memory",
        "temporary_memory",
        "archive_metadata",
        "archive_partition",
        "guest_mode",
        "delayed_tools",
        "model_catalogue",
        "quiet_hours",
        "broadcast_settings",
    ),
)
async def test_transactional_store_writer_failure_is_observable_and_retryable(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    real_store_io: None,
    owner: str,
) -> None:
    """Every transaction owner must see HA's real atomic writer failure."""
    del real_store_io
    archive = HomeAssistantArchiveStorage(hass, "disk-entry", "disk-agent")
    stores = {
        "persistent_memory": HomeAssistantMemoryStorage(
            hass, "disk-entry", "disk-agent"
        )._store,
        "temporary_memory": _temporary_memory_store(hass, "disk-entry", "disk-agent"),
        "archive_metadata": archive._metadata,
        "archive_partition": archive._partition_store("2026-09"),
        "guest_mode": GuestModeManager(hass, "disk-entry", "disk-agent")._store,
        "delayed_tools": DelayedToolManager(hass)._store,
        "model_catalogue": ModelCatalogManager(hass).store,
        "quiet_hours": QuietHoursManager(hass)._store,
        "broadcast_settings": IntercomManager(hass)._store,
    }
    store = stores[owner]
    await store.async_save({"generation": "A"})
    before = Path(store.path).read_bytes()
    with monkeypatch.context() as fault:
        if store._atomic_writes:
            fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.EACCES))
        else:
            fault.setattr(ha_file.os, "replace", _raise_os_error(errno.EACCES))
        with pytest.raises(OSError) as error:
            await store.async_save({"generation": "B"})
    assert error.value.errno == errno.EACCES
    assert Path(store.path).read_bytes() == before
    assert await store.async_load() == {"generation": "A"}
    await store.async_save({"generation": "C"})
    assert await store.async_load() == {"generation": "C"}
    record(
        stress_trace,
        "durable_mutation",
        owner=owner,
        classification="transactional_durable",
        failure_phase="atomic_replace",
        runtime_rolled_back=True,
        reload_preserved_prior_generation=True,
        recovery_write_succeeded=True,
    )


async def test_temporary_memory_failure_does_not_publish_or_reload_new_fact(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    real_store_io: None,
) -> None:
    del real_store_io
    manager = TemporaryMemory(_temporary_memory_store(hass, "temp-entry", "temp-agent"))
    await manager.async_initialize()
    expiry = (dt_util.utcnow() + timedelta(days=1)).isoformat()
    first = await manager.async_add(
        "scope", "Known fact", expiry, owner_scope_id="user:alice"
    )
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.ENOSPC))
        with pytest.raises(OSError, match="Private storage write failed"):
            await manager.async_add(
                "scope", "Undurable fact", expiry, owner_scope_id="user:alice"
            )
    assert len(manager._records) == 1
    assert first["memory"]["memory_id"] in manager._records
    restarted = TemporaryMemory(
        _temporary_memory_store(hass, "temp-entry", "temp-agent")
    )
    await restarted.async_initialize()
    assert set(restarted._records) == set(manager._records)
    await manager.async_add(
        "scope", "Recovered fact", expiry, owner_scope_id="user:alice"
    )
    assert len(manager._records) == 2


async def test_guest_schedule_failure_does_not_publish_and_retries(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    real_store_io: None,
) -> None:
    del real_store_io
    manager = GuestModeManager(hass, "guest-entry", "guest-agent")
    await manager.async_initialize()
    assert manager._schedule is None
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.EROFS))
        with pytest.raises(OSError, match="Private storage write failed"):
            await manager.async_update_trusted(indefinite=True)
    assert manager._schedule is None
    restarted = GuestModeManager(hass, "guest-entry", "guest-agent")
    await restarted.async_initialize()
    assert restarted._schedule is None
    await manager.async_update_trusted(indefinite=True)
    assert manager._schedule is not None


async def test_broadcast_switch_failure_does_not_report_enabled(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    real_store_io: None,
) -> None:
    del real_store_io
    manager = IntercomManager(hass)
    await manager.async_initialize()
    assert manager.enabled is False
    with monkeypatch.context() as fault:
        fault.setattr(ha_file.os, "replace", _raise_os_error(errno.EACCES))
        with pytest.raises(OSError, match="Private storage write failed"):
            await manager.async_set_enabled(True)
    assert manager.enabled is False
    restarted = IntercomManager(hass)
    await restarted.async_initialize()
    assert restarted.enabled is False
    await manager.async_set_enabled(True)
    assert manager.enabled is True


async def _usage_with_rows(hass, monkeypatch):
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

    agent = await _agent(hass, title="Durable Usage operations")
    _install_wire(monkeypatch, agent, [_chat_sse_text("Accounted")])
    assert _speech(await _say(hass, agent, "Create genuine usage rows")) == "Accounted"
    await hass.async_block_till_done()
    usage = agent._usage
    await usage._async_save_aggregates()
    await usage._async_save_details()
    assert len(usage.requests) == len(usage.runs) == 1
    return agent, usage


async def _fresh_usage(hass, usage):
    from custom_components.extended_openai_conversation_responses.usage import (
        UsageManager,
        _UsageStore,
    )

    def store(original):
        return _UsageStore(
            hass,
            original.version,
            original.key,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    fresh = UsageManager(
        store(usage._storage),
        store(usage._daily_storage),
        store(usage._detail_storage),
        agent_subentry_id=usage._agent_subentry_id,
    )
    await fresh.async_initialize()
    return fresh


@pytest.mark.parametrize("operation", ["clear", "prune"])
@pytest.mark.parametrize(
    "seam,number", [("replace", errno.EACCES), ("fsync", errno.ENOSPC)]
)
async def test_usage_explicit_deletion_requires_real_durable_write(
    hass, monkeypatch, real_store_io, stress_trace, operation, seam, number
):
    from copy import deepcopy
    import json

    from custom_components.extended_openai_conversation_responses import (
        usage as usage_module,
    )
    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

    agent, usage = await _usage_with_rows(hass, monkeypatch)
    path = Path(usage._detail_storage.path)
    before = path.read_bytes()
    rows = deepcopy((usage.requests, usage.runs))
    old_prune_date = usage._last_prune_date
    old_retry = usage._next_prune_retry
    # A queued telemetry snapshot must not restore deleted rows after a clear.
    old_snapshot = json.loads(before)["data"]
    usage._detail_storage.async_delay_save(lambda: old_snapshot, 60)
    base = dt_util.utcnow()

    async def delete():
        if operation == "prune":
            return await usage.async_prune_details()
        return await async_management_command(
            hass,
            None,
            True,
            {
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "section": "usage",
                "action": "clear_details",
                "confirm": True,
            },
        )

    with monkeypatch.context() as clock:
        if operation == "prune":
            clock.setattr(
                usage_module.dt_util, "utcnow", lambda: base + timedelta(days=400)
            )
        with monkeypatch.context() as fault:
            if seam == "replace":
                replace = atomicwrites.replace_atomic

                def fail_target(source, destination):
                    if Path(destination) == path:
                        raise OSError(number, "Usage destination cannot be replaced")
                    return replace(source, destination)

                fault.setattr(atomicwrites, "replace_atomic", fail_target)
            else:
                fault.setattr(atomicwrites, "_proper_fsync", _raise_os_error(number))
            with pytest.raises(OSError) as error:
                await delete()
            assert error.value.errno == number
            assert (usage.requests, usage.runs) == rows
            assert usage._last_prune_date == old_prune_date
            assert usage._next_prune_retry == old_retry
            assert path.read_bytes() == before
            # Compare the stored generation at its original retention clock.
            with monkeypatch.context() as read_clock:
                read_clock.setattr(usage_module.dt_util, "utcnow", lambda: base)
                fresh = await _fresh_usage(hass, usage)
            assert len(fresh.requests) == len(fresh.runs) == 1
        result = await delete()
        assert result == {"deleted_requests": 1, "deleted_runs": 1}
        assert usage.requests == usage.runs == []
        assert json.loads(path.read_bytes())["data"] == {"requests": [], "runs": []}
        assert usage._detail_storage._delay_handle is None
        # Even a surviving native callback has no pre-clear snapshot to publish.
        await usage._detail_storage._async_callback_delayed_write()
        fresh = await _fresh_usage(hass, usage)
        assert fresh.requests == fresh.runs == []
        assert json.loads(path.read_bytes())["data"] == {"requests": [], "runs": []}
    _install_wire(monkeypatch, agent, [_chat_sse_text("Healthy after retry")])
    assert (
        _speech(await _say(hass, agent, "Healthy after retry")) == "Healthy after retry"
    )
    record(
        stress_trace,
        "summary",
        usage_durable_deletions=1,
        usage_writer_failures=1,
        usage_fresh_reads=2,
        recovery_conversations=1,
    )


async def test_usage_delayed_writer_failure_keeps_foreground_accounting_best_effort(
    hass, monkeypatch, real_store_io, stress_trace
):
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

    agent, usage = await _usage_with_rows(hass, monkeypatch)
    before = Path(usage._detail_storage.path).read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", _raise_os_error(errno.ENOSPC))
        _install_wire(monkeypatch, agent, [_chat_sse_text("Foreground succeeds")])
        assert (
            _speech(await _say(hass, agent, "Telemetry remains optional"))
            == "Foreground succeeds"
        )
        # Exercise HA's actual delayed callback, below the immediate-save error boundary.
        for store in (usage._storage, usage._daily_storage, usage._detail_storage):
            await store._async_callback_delayed_write()
        assert len(usage.requests) == len(usage.runs) == 2
        assert Path(usage._detail_storage.path).read_bytes() == before
    await usage._async_save_aggregates()
    await usage._async_save_details()
    fresh = await _fresh_usage(hass, usage)
    assert len(fresh.requests) == len(fresh.runs) == 2
    record(
        stress_trace,
        "summary",
        best_effort_usage_writer_failures=3,
        recovery_usage_flushes=1,
    )


@pytest.mark.parametrize("category", ["daily", "details"])
async def test_usage_restore_writer_failure_retains_recovery_evidence(
    hass, monkeypatch, real_store_io, stress_trace, category
):
    import json

    from custom_components.extended_openai_conversation_responses import (
        backup,
        restore_recovery,
    )
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        get_agent_maintenance_gate,
    )

    agent, usage = await _usage_with_rows(hass, monkeypatch)
    entry, subentry = agent.entry, agent.subentry
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    previous = await usage.async_backup_data()
    target["usage"]["requests"] = []
    target["usage"]["runs"] = []
    path = Path(
        getattr(usage, f"_{'daily' if category == 'daily' else 'detail'}_storage").path
    )
    before = path.read_bytes()
    replace = atomicwrites.replace_atomic
    faults = []

    def fail_target(source, destination):
        if Path(destination) == path:
            faults.append(category)
            raise OSError(errno.EROFS, "Usage restore destination is read-only")
        return replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "replace_atomic", fail_target)
        with pytest.raises(backup.BackupError, match="pending"):
            await backup.async_restore_backup(hass, entry, subentry, target)
        assert len(faults) >= 2  # Both application and rollback encountered the fault.
        assert path.read_bytes() == before
        journal = restore_recovery._journal_store(
            hass, entry.entry_id, subentry.subentry_id
        )
        assert (
            json.loads(Path(journal.path).read_bytes())["data"]["phase"] == "applying"
        )
        assert get_agent_maintenance_gate(
            hass, entry.entry_id, subentry.subentry_id
        ).recovery_required
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert not Path(journal.path).exists()
    fresh = await _fresh_usage(hass, usage)
    assert await fresh.async_backup_data() == previous
    assert not get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    ).recovery_required
    record(
        stress_trace,
        "summary",
        usage_restore_writer_faults=1,
        usage_restore_recoveries=1,
    )


async def test_usage_explicit_clear_during_shutdown_finishes_native_write(
    hass, monkeypatch, real_store_io, stress_trace
):
    import json

    from homeassistant.core import CoreState

    _, usage = await _usage_with_rows(hass, monkeypatch)
    original_state = hass.state
    try:
        hass.set_state(CoreState.stopping)
        result = await usage.async_clear_details(confirm=True)
        assert result == {"deleted_requests": 1, "deleted_runs": 1}
        assert json.loads(Path(usage._detail_storage.path).read_bytes())["data"] == {
            "requests": [],
            "runs": [],
        }
        assert usage._detail_storage._data is None
    finally:
        hass.set_state(original_state)
    fresh = await _fresh_usage(hass, usage)
    assert fresh.requests == fresh.runs == []
    record(stress_trace, "summary", usage_shutdown_durable_clears=1)
