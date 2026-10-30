"""Bulk restore categories are detached and validated outside HA's loop."""

from copy import deepcopy
from dataclasses import asdict
import threading
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from tests.restore_participants import participants
from tests.test_backup import _document
from tests.test_conversation_archive import _session, _turn


def category_document():
    document = _document()
    document["memories"]["memories"] = [
        {**document["memories"]["memories"][0], "memory_id": f"memory-{i}"}
        for i in range(5000)
    ]
    document["knowledge"]["sources"] = [
        {
            **document["knowledge"]["sources"][0],
            "source_id": f"source-{i}",
            "content": "House reference information. " * 350,
        }
        for i in range(300)
    ]
    document["archive"] = {
        "sessions": [asdict(_session(turn_count=6000))],
        "turns": [asdict(_turn(turn_id=f"turn-{i}")) for i in range(6000)],
    }
    return document


@pytest.mark.parametrize("route", ["backup", "full", "custom", "snapshot"])
async def test_bulk_category_validation_uses_workers(monkeypatch, route):
    document = category_document()
    loop_thread = threading.get_ident()
    observations = {}
    for key, cls in (
        ("memories", backup.PersistentMemory),
        ("temporary_memories", backup.TemporaryMemory),
        ("knowledge", backup.KnowledgeLibrary),
        ("archive", backup.ConversationArchive),
    ):
        original = cls.validate_backup_data

        def observe(*args, _key=key, _original=original, **kwargs):
            start = perf_counter()
            result = _original(*args, **kwargs)
            observations.setdefault(_key, []).append(
                (threading.get_ident(), perf_counter() - start)
            )
            return result

        monkeypatch.setattr(cls, "validate_backup_data", staticmethod(observe))
    if route == "backup":
        await backup.async_inspect_backup(document, "agent-new")
    elif route == "snapshot":
        categories = {
            "memory": "memories",
            "temporary": "temporary_memories",
            "knowledge": "knowledge",
            "archive": "archive",
            "usage": "usage",
            "guest_mode": "guest_mode",
            "request_rules": "request_rules",
        }
        document.setdefault("guest_mode", {"schedule": None})
        managers = participants(
            **{
                name: SimpleNamespace(
                    async_backup_data=AsyncMock(return_value=document[key])
                )
                for name, key in categories.items()
            }
        )
        managers.archive.async_recovery_data = AsyncMock(
            return_value={**document["archive"], "active": {}}
        )
        await backup._snapshot_for_restore(
            managers,
            SimpleNamespace(
                subentry_id="agent-new", title="Test", data=document["agent"]["config"]
            ),
        )
    else:
        if route == "custom":
            document = {
                "format": transfer.TRANSFER_FORMAT,
                "version": 1,
                "mode": "custom",
                "created_at": document["created_at"],
                "integration_version": "1",
                "agent": {k: v for k, v in document["agent"].items() if k != "config"},
                "sections": {
                    "persistent_memory": document["memories"],
                    "temporary_memory": document["temporary_memories"],
                    "knowledge": document["knowledge"],
                    "conversation_archive": document["archive"],
                },
            }
        await transfer.async_inspect_decoded_transfer(document, "agent-new")
    print(route, observations)
    assert observations.keys() == {
        "memories",
        "temporary_memories",
        "knowledge",
        "archive",
    }
    assert all(
        thread != loop_thread for calls in observations.values() for thread, _ in calls
    ), observations


@pytest.mark.parametrize(
    "selected", [transfer.ALL_SECTIONS, frozenset({transfer.SECTION_USAGE})]
)
async def test_mapped_and_materialized_data_are_deeply_isolated(monkeypatch, selected):
    prepared = transfer.inspect_transfer(_document(), "agent-new")
    selected = selected & prepared.available_sections
    before = deepcopy(prepared)
    mapped = await transfer.async_apply_user_scope_mappings(
        prepared, selected, {"user-1": "user-2"}
    )
    mapped.usage_requests[0].details["changed"] = 1
    mapped.usage_runs[0].models.append("changed")
    mapped.config["function_groups"][0]["functions"].append("changed")
    mapped.raw_configuration["function_groups"][0]["functions"].append("changed")
    mapped.request_rules["rules"].clear()
    mapped.memories.clear()
    assert prepared == before
    current = backup.inspect_backup(_document(), "agent-new")
    monkeypatch.setattr(
        transfer, "_async_validate_request_rule_function_dependencies", AsyncMock()
    )
    target, _ = await transfer.async_materialize_restore(
        SimpleNamespace(),
        SimpleNamespace(data={}),
        SimpleNamespace(subentry_id="agent-new"),
        prepared,
        sections=selected,
        current_snapshot=current,
    )
    target.usage_requests[0].details["changed"] = 1
    target.usage_runs[0].models.append("changed")
    target.config["function_groups"][0]["functions"].append("changed")
    target.memories.clear()
    target.request_rules["rules"].clear()
    assert prepared == before
