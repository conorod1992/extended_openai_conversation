"""Real-HA lifecycle and isolation coverage across agents and subentries."""

from __future__ import annotations

import asyncio
from types import MappingProxyType

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_AI_TASK_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_ai_task_runtime import FakeClient
from tests_real_ha.test_live_subentry_removal import (
    _entry as _mixed_entry,
    _setup_entry as _setup_mixed,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _fresh_reload,
    _management_call,
    _setup_entry,
)
from tests_stress.conftest import record


def _conversation_subentry_by_id(entry, subentry_id):
    return next(
        item for item in entry.subentries.values()
        if item.subentry_id == subentry_id
    )


@pytest.mark.asyncio
async def test_duplicate_and_imported_agents_start_config_only_then_diverge_durably(
    hass: HomeAssistant,
    hass_ws_client,
    stress_trace: list[dict],
) -> None:
    """Duplicate/new-import copy config, never private runtime data, then isolate state."""
    source = _entry("Transfer source installation")
    destination = _entry("Transfer destination installation")
    await _setup_entry(hass, source)
    await _setup_entry(hass, destination)
    client = await _admin_client(hass, hass_ws_client)

    source_cfg = await _management_call(
        client, entry=source, section="configuration", action="get"
    )
    await _management_call(
        client,
        entry=source,
        section="configuration",
        action="save",
        revision=source_cfg["revision"],
        config={"max_tokens": 913, "prompt": "Portable source configuration"},
    )
    await _management_call(
        client,
        entry=source,
        section="memories",
        action="add",
        content="SOURCE_PRIVATE_RUNTIME_MARKER",
        category="transfer",
    )

    duplicate = await _management_call(
        client,
        entry=source,
        section="configuration",
        action="duplicate",
        title="Duplicated assistant",
    )
    duplicate_id = duplicate["subentry_id"]
    duplicate_cfg = await _management_call(
        client,
        entry=source,
        subentry_id=duplicate_id,
        section="configuration",
        action="get",
    )
    assert duplicate_cfg["config"]["max_tokens"] == 913
    assert duplicate_cfg["config"]["prompt"] == "Portable source configuration"
    duplicate_memories = await _management_call(
        client,
        entry=source,
        subentry_id=duplicate_id,
        section="memories",
        action="list",
    )
    assert duplicate_memories["memories"] == []

    exported = await _management_call(
        client, entry=source, section="configuration", action="export"
    )
    imported = await _management_call(
        client,
        entry=destination,
        section="configuration",
        action="import",
        document=exported["document"],
        mode="new",
    )
    imported_id = imported["subentry_id"]
    imported_cfg = await _management_call(
        client,
        entry=destination,
        subentry_id=imported_id,
        section="configuration",
        action="get",
    )
    assert imported_cfg["config"]["max_tokens"] == 913
    assert imported_cfg["config"]["prompt"] == "Portable source configuration"
    imported_memories = await _management_call(
        client,
        entry=destination,
        subentry_id=imported_id,
        section="memories",
        action="list",
    )
    assert imported_memories["memories"] == []

    await _management_call(
        client,
        entry=source,
        subentry_id=duplicate_id,
        section="memories",
        action="add",
        content="DUPLICATE_PRIVATE_RUNTIME_MARKER",
        category="transfer",
    )
    await _management_call(
        client,
        entry=destination,
        subentry_id=imported_id,
        section="memories",
        action="add",
        content="IMPORTED_PRIVATE_RUNTIME_MARKER",
        category="transfer",
    )

    await _fresh_reload(hass, source)
    await _fresh_reload(hass, destination)

    source_after = await _management_call(
        client, entry=source, section="memories", action="list"
    )
    duplicate_after = await _management_call(
        client,
        entry=source,
        subentry_id=duplicate_id,
        section="memories",
        action="list",
    )
    imported_after = await _management_call(
        client,
        entry=destination,
        subentry_id=imported_id,
        section="memories",
        action="list",
    )
    assert [item["content"] for item in source_after["memories"]] == [
        "SOURCE_PRIVATE_RUNTIME_MARKER"
    ]
    assert [item["content"] for item in duplicate_after["memories"]] == [
        "DUPLICATE_PRIVATE_RUNTIME_MARKER"
    ]
    assert [item["content"] for item in imported_after["memories"]] == [
        "IMPORTED_PRIVATE_RUNTIME_MARKER"
    ]
    record(
        stress_trace,
        "summary",
        layer="Real HA Management",
        duplicated_agents=1,
        cross_parent_imports=1,
        private_state_isolation_probes=6,
        fresh_reloads=2,
    )


@pytest.mark.asyncio
async def test_backup_snapshot_of_one_agent_is_stable_while_sibling_mutates(
    hass: HomeAssistant,
    hass_ws_client,
    stress_trace: list[dict],
) -> None:
    """Heavy sibling writes cannot bleed into another assistant's backup snapshot."""
    entry = _entry("Sibling backup isolation")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    original = next(iter(entry.subentries.values()))
    duplicate = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="duplicate",
        title="Busy sibling",
    )
    sibling = _conversation_subentry_by_id(entry, duplicate["subentry_id"])

    target_memory = await async_get_memory(hass, entry.entry_id, original.subentry_id)
    sibling_memory = await async_get_memory(hass, entry.entry_id, sibling.subentry_id)
    await target_memory.async_add(
        "backup-owner", "TARGET_BACKUP_MARKER", "backup", "explicit"
    )

    async def churn() -> None:
        for index in range(30):
            await sibling_memory.async_add(
                "backup-owner",
                f"SIBLING_MUTATION_{index:02d}",
                "backup",
                "explicit",
            )
            if index % 3 == 0:
                records = await sibling_memory.async_list("backup-owner")
                if len(records) > 5:
                    await sibling_memory.async_delete(
                        "backup-owner", [records[0].memory_id]
                    )
            await asyncio.sleep(0)

    churn_task = asyncio.create_task(churn())
    snapshots = []
    while not churn_task.done():
        snapshots.append(
            await backup.async_collect_backup_snapshot(hass, entry, original)
        )
        await asyncio.sleep(0)
    await churn_task
    snapshots.append(await backup.async_collect_backup_snapshot(hass, entry, original))

    assert snapshots
    for snapshot in snapshots:
        serialized = str(snapshot)
        assert "TARGET_BACKUP_MARKER" in serialized
        assert "SIBLING_MUTATION_" not in serialized

    final_target = [
        item.content for item in await target_memory.async_list("backup-owner")
    ]
    assert final_target == ["TARGET_BACKUP_MARKER"]
    assert len(await sibling_memory.async_list("backup-owner")) > 1
    record(
        stress_trace,
        "summary",
        layer="Real HA stores + backup",
        sibling_mutations=30,
        isolated_backup_snapshots=len(snapshots),
    )


@pytest.mark.asyncio
async def test_ai_task_delete_recreate_keeps_active_conversation_sibling_usable(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """AI Task recreation must not reset or contaminate an active Conversation."""
    entry = _mixed_entry()
    await _setup_mixed(hass, entry)
    conversation_subentry = next(
        item for item in entry.subentries.values()
        if item.subentry_type == "conversation" and item.title == "Conversation B"
    )
    conversation_row = next(
        row for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == "conversation"
        and row.config_subentry_id == conversation_subentry.subentry_id
    )
    agent = conversation.async_get_agent(hass, conversation_row.entity_id)
    assert agent is not None

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_model(log, **kwargs):
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content="conversation survived task recreation",
            )
        )

    monkeypatch.setattr(agent, "_async_handle_chat_log", blocked_model)
    active = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="hold conversation while AI Task is recreated",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=conversation_row.entity_id,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=10)

    old_task = next(
        item for item in entry.subentries.values() if item.subentry_type == "ai_task_data"
    )
    old_entity = er.async_get(hass).async_get_entity_id(
        ai_task.DOMAIN,
        "extended_openai_conversation_responses",
        old_task.subentry_id,
    )
    assert old_entity
    assert hass.config_entries.async_remove_subentry(entry, old_task.subentry_id)
    await hass.async_block_till_done()

    recreated = ConfigSubentry(
        data=MappingProxyType(dict(DEFAULT_AI_TASK_OPTIONS)),
        subentry_type="ai_task_data",
        title="AI Task Recreated",
        unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(entry, recreated)
    await hass.async_block_till_done()
    new_entity = er.async_get(hass).async_get_entity_id(
        ai_task.DOMAIN,
        "extended_openai_conversation_responses",
        recreated.subentry_id,
    )
    assert new_entity and new_entity != old_entity

    client = FakeClient(["recreated task works"])
    entry.runtime_data = client
    task_result = await ai_task.async_generate_data(
        hass,
        task_name="Recreated task probe",
        entity_id=new_entity,
        instructions="Return a health marker.",
    )
    assert task_result.data == "recreated task works"

    release.set()
    conversation_result = await asyncio.wait_for(active, timeout=15)
    assert (
        conversation_result.response.as_dict()["speech"]["plain"]["speech"]
        == "conversation survived task recreation"
    )
    # Parent entry updates reload its platforms. The active turn above must
    # finish safely, and the reconstructed sibling must remain ready for use.
    current_agent = conversation.async_get_agent(hass, conversation_row.entity_id)
    assert current_agent is not None and current_agent._agent_ready.is_set()
    assert current_agent.subentry.subentry_id == conversation_subentry.subentry_id
    monkeypatch.setattr(current_agent, "_async_handle_chat_log", blocked_model)
    resumed = await conversation.async_converse(
        hass=hass,
        text="check the reconstructed conversation sibling",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=conversation_row.entity_id,
    )
    assert (
        resumed.response.as_dict()["speech"]["plain"]["speech"]
        == "conversation survived task recreation"
    )
    record(
        stress_trace,
        "summary",
        layer="Real HA Conversation + AI Task",
        active_conversation_during_task_recreation=1,
        ai_task_recreations=1,
        public_task_calls=1,
    )


@pytest.mark.asyncio
async def test_one_conversation_initialization_failure_does_not_poison_healthy_sibling(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A broken conversation child must not make a separately valid sibling unusable."""
    entry = _mixed_entry()
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    original = ExtendedOpenAIAgentEntity._async_initialize_agent_state

    async def selective_failure(self):
        if self.subentry.title == "Conversation A":
            raise RuntimeError("controlled broken sibling")
        return await original(self)

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_initialize_agent_state",
        selective_failure,
    )
    # HA entity-platform isolation should allow the parent to finish setup even
    # when one child cannot initialize.
    await _setup_mixed(hass, entry)

    healthy = next(
        item for item in entry.subentries.values()
        if item.subentry_type == "conversation" and item.title == "Conversation B"
    )
    healthy_row = next(
        row for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == "conversation" and row.config_subentry_id == healthy.subentry_id
    )
    healthy_agent = conversation.async_get_agent(hass, healthy_row.entity_id)
    assert healthy_agent is not None
    assert healthy_agent._agent_ready.is_set()
    assert not healthy_agent._agent_initialization_failed
    record(
        stress_trace,
        "summary",
        layer="Real HA entity platform",
        broken_conversation_children=1,
        healthy_sibling_probes=1,
    )
