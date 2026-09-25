"""Every durable restore category must roll back as one transaction."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    backup,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    async_get_guest_mode,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    async_get_temporary_memory,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_durable_usage,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _raw_client,
    _responses_sse_text,
    _speech,
)
from tests_stress.conftest import record
from tests_stress.maximal_agent_fixture import FIXTURE_EXCEPTIONS, maximal_agent_options
from tests_stress.test_backup_inventory import BACKED_UP_SUBSYSTEMS

PHASES = (
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
)


@pytest.mark.asyncio
async def test_populated_export_mutate_restore_is_semantically_equal(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    maximal = maximal_agent_options()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Round trip",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": maximal,
                "subentry_type": "conversation",
                "title": "Archive 🎯 agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    blank = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    temporary = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    usage = await async_get_durable_usage(hass, entry.entry_id, subentry.subentry_id)
    await memory.async_add(
        "owner", "Distinct private memory 東京", "acceptance", "explicit"
    )
    await knowledge.async_create(
        "Reference 🎯", "multiline description", 'Line one\n{"json": true}\nLine three'
    )
    await rules.async_create(
        {
            "name": "Rule café",
            "phrases": ["remember {fact}"],
            "match_type": "sentence_pattern",
            "action_type": "local_action",
            "action": {"actions": [{"action": "script.turn_on"}]},
        }
    )
    await temporary.async_add(
        "user:owner",
        "Temporary round-trip marker 🕒",
        (dt_util.utcnow() + timedelta(hours=2)).isoformat(),
        "acceptance",
        owner_scope_id="user:owner",
    )
    await guest.async_update_trusted(indefinite=True)
    async with usage.async_run(home_assistant_conversation_id="backup-journey"):
        await usage.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=12, output_tokens=4, total_tokens=16),
            provider="openai",
            model="gpt-5.6",
            api_mode="chat_completions",
            request_stage="initial",
            tool_calls_requested=0,
        )
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    defaults = agent_config.agent_config_defaults()
    nondefault_config_fields = sum(
        value != defaults.get(key) for key, value in target["agent"]["config"].items()
    )
    assert nondefault_config_fields == len(agent_config.AGENT_CONFIG_FIELDS) - len(
        FIXTURE_EXCEPTIONS
    )
    assert (
        set(target) - {"format", "version", "created_at", "integration_version"}
        == BACKED_UP_SUBSYSTEMS
    )
    record(
        stress_trace,
        "export",
        memory_records=1,
        knowledge_sources=1,
        request_rules=1,
        temporary_memories=1,
        guest_mode_schedules=1,
        usage_requests=1,
        nondefault_config_fields=nondefault_config_fields,
    )
    assert (await backup.async_restore_backup(hass, entry, subentry, blank))[
        "status"
    ] == "restored"
    hass.config_entries.async_update_subentry(
        entry, subentry, title="Mutated agent", data={}
    )
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) != semantic(target)
    record(stress_trace, "mutate_all_populated_features")

    assert (await backup.async_restore_backup(hass, entry, subentry, target))[
        "status"
    ] == "restored"
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) == semantic(target)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) == semantic(target)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_text("Guest backup restored"),
            _responses_sse_text("Owner backup restored"),
        ],
    )
    embedding_requests = []
    scripted_send = wire.send

    async def send(request, *args, **kwargs):
        if request.url.path == "/v1/embeddings":
            body = json.loads(request.content)
            embedding_requests.append(body)
            inputs = body["input"]
            if isinstance(inputs, str):
                inputs = [inputs]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [0.5] * 8}
                        for index, _ in enumerate(inputs)
                    ],
                    "usage": {
                        "prompt_tokens": len(inputs),
                        "total_tokens": len(inputs),
                    },
                },
                request=request,
            )
        return await scripted_send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    guest_result = await conversation.async_converse(
        hass=hass,
        text="Confirm backup marker",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(guest_result) == "Guest backup restored"
    assert agent._guest_mode.is_active()
    guest_request = wire.requests[0]["body"]
    assert "load_function_groups" not in str(guest_request)
    assert "Distinct private memory" not in str(guest_request)
    await agent._guest_mode.async_disable_trusted()
    owner_result = await conversation.async_converse(
        hass=hass,
        text="Confirm backup marker",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(owner_result) == "Owner backup restored"
    assert len(wire.requests) == 2
    assert embedding_requests
    owner_request = wire.requests[1]["body"]
    assert "Preserve café 🎯" in str(owner_request)
    assert "load_function_groups" in str(owner_request)
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        restore_round_trips=1,
        reloads=1,
        nondefault_config_fields=nondefault_config_fields,
        public_turns=2,
        provider_requests=2 + len(embedding_requests),
        embedding_provider_requests=len(embedding_requests),
    )


def semantic(snapshot: dict) -> dict:
    result = deepcopy(snapshot)
    result.pop("created_at", None)
    return result


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.asyncio
async def test_every_restore_phase_rolls_back_and_reloads(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    stress_trace: list[dict],
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fault matrix",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Fault matrix agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)

    await memory.async_add("owner", "target memory 🎯", "acceptance", "explicit")
    await knowledge.async_create(
        "Target knowledge", "before mutation", "target content"
    )
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert (
        set(target) - {"format", "version", "created_at", "integration_version"}
        == BACKED_UP_SUBSYSTEMS
    )
    for item in await memory.async_list("owner"):
        assert await memory.async_delete("owner", [item.memory_id]) == 1
    for item in await knowledge.async_list():
        assert await knowledge.async_delete(item["source_id"])
    await memory.async_add("owner", "current memory", "acceptance", "explicit")
    await knowledge.async_create(
        "Current knowledge", "after mutation", "current content"
    )
    before = semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
    assert before != semantic(target)

    manager_getters = {
        "memory": async_get_memory,
        "temporary_memory": async_get_temporary_memory,
        "knowledge": async_get_knowledge,
        "archive": async_get_archive,
        "usage": async_get_durable_usage,
        "guest_mode": async_get_guest_mode,
        "request_rules": async_get_request_rules,
    }
    manager = await manager_getters[phase](hass, entry.entry_id, subentry.subentry_id)
    original = manager.async_replace_backup
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f"injected {phase} apply failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "async_replace_backup", fail_once)
    record(stress_trace, "restore_fault", phase=phase)
    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await backup.async_restore_backup(hass, entry, subentry, target)
    assert calls == 2
    await hass.async_block_till_done()
    assert (
        semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
        == before
    )
    assert entry.state is ConfigEntryState.LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
        == before
    )
    record(stress_trace, "summary", rollback_phases=1, reloads=1)
