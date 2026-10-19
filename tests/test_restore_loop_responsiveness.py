"""Real Usage histories must not monopolize the loop during journaled restore."""

import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    RequestUsage,
)
from tests.test_backup import _document
from tests.test_restore_recovery import FakeHass


class WorkerStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return await asyncio.to_thread(deepcopy, self.data)

    async def async_save(self, data):
        self.data = await asyncio.to_thread(deepcopy, data)

    async def async_remove(self):
        self.data = None


@pytest.mark.parametrize("kind", ["prepared", "legacy"])
@pytest.mark.parametrize("count", [0, 2000, 2000])
async def test_restore_keeps_unrelated_heartbeat_responsive(monkeypatch, count, kind):
    # Seed legitimate records through the accounting API, outside the timed interval.
    manager = UsageManager(WorkerStore(), agent_subentry_id="agent-new")
    await manager.async_initialize()
    for _ in range(count):
        async with manager.async_run():
            await manager.async_record_request(
                successful=True,
                usage=RequestUsage(input_tokens=30, output_tokens=10, total_tokens=40),
            )
    document = _document()
    document["usage"] = await manager.async_backup_data()
    document.setdefault("guest_mode", {"schedule": None})
    document.setdefault("request_rules", {"defaults": {}, "rules": []})
    target = backup.inspect_backup(document, "agent-new")
    categories = [
        "memories",
        "temporary_memories",
        "knowledge",
        "archive",
        "usage",
        "guest_mode",
        "request_rules",
    ]
    managers = tuple(
        manager
        if key == "usage"
        else SimpleNamespace(
            async_backup_data=AsyncMock(return_value=document[key]),
            async_replace_backup=AsyncMock(),
        )
        for key in categories
    )
    managers[3].async_recovery_data = AsyncMock(
        return_value={**document["archive"], "active": {}}
    )
    hass = FakeHass()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title=target.title, data=deepcopy(target.config)
    )
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: WorkerStore())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", lambda *_: None)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(
        transfer, "_async_validate_request_rule_function_dependencies", AsyncMock()
    )
    loop_thread = threading.get_ident()
    worker_threads = []
    build = restore_recovery._new_journal

    def observed_build(*args):
        worker_threads.append(threading.get_ident())
        return build(*args)

    monkeypatch.setattr(restore_recovery, "_new_journal", observed_build)
    gaps = []
    running = True

    async def heartbeat():
        previous = asyncio.get_running_loop().time()
        while running:
            await asyncio.sleep(0.005)
            now = asyncio.get_running_loop().time()
            gaps.append(now - previous)
            previous = now

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    try:
        result = await backup.async_restore_backup(
            hass, entry, subentry, target if kind == "prepared" else document
        )
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(0.01)
    finally:
        running = False
        await pulse
    print(
        f"kind={kind} requests={count} restore={elapsed:.3f}s heartbeat={max(gaps):.3f}s"
    )
    assert result["status"] == "restored"
    assert len(manager.requests) == count
    assert manager.totals.total_tokens == count * 40
    assert max(gaps) < 0.25
    if count:
        assert worker_threads and all(item != loop_thread for item in worker_threads)


async def test_async_inspection_validates_usage_in_worker_but_configuration_on_loop(
    monkeypatch,
):
    loop_thread = threading.get_ident()
    usage_threads, config_threads = [], []
    usage_validate = UsageManager.validate_backup_data
    config_validate = backup.recoverable_configuration_snapshot

    def usage_validation(*args):
        usage_threads.append(threading.get_ident())
        return usage_validate(*args)

    def config_validation(*args):
        config_threads.append(threading.get_ident())
        return config_validate(*args)

    monkeypatch.setattr(
        UsageManager, "validate_backup_data", staticmethod(usage_validation)
    )
    monkeypatch.setattr(backup, "recoverable_configuration_snapshot", config_validation)
    await backup.async_inspect_backup(_document(), "agent-new")
    assert usage_threads and all(thread != loop_thread for thread in usage_threads)
    assert config_threads == [loop_thread]
