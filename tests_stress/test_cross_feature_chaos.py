"""Seeded valid mutations across independent durable agent stores and reloads."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import timedelta
import json
import random
from uuid import uuid4

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
import yaml

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPORARY_MEMORY,
    CONFIG_ENTRY_VERSION,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DOMAIN,
    GUEST_POLICY_VERSION,
    MEMORY_MODE_MANUAL,
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
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _raw_client,
)
from tests_stress.behaviour_oracles import last_tool_result
from tests_stress.conftest import record
from tests_stress.expected_effects import ExpectedEffects
from tests_stress.health import HealthChecks, assert_enhanced_health
from tests_stress.test_provider_protocol_acceptance import (
    _assert_valid_outgoing_history,
)


def _semantic(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "created_at"}


@pytest.mark.asyncio
async def test_seeded_cross_store_chaos_preserves_valid_agent_state(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    hass_ws_client,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    rng = random.Random(stress_seed ^ 0xC4A05)
    for number in range(4):
        MockUser(id=f"chaos-user-{number}", name=f"Chaos user {number}").add_to_hass(
            hass
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Chaos agent",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {
                    "api_mode": "chat_completions",
                    "chat_model": "gpt-5.6",
                    "reasoning_effort": "none",
                    "max_tokens": 600,
                    "guest_mode_enabled": True,
                    "guest_policy_version": GUEST_POLICY_VERSION,
                    "guest_function_policy": "off",
                    "guest_knowledge_policy": "off",
                    "guest_shared_memory_policy": "off",
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                    CONF_KNOWLEDGE_ENABLED: True,
                    CONF_TEMPORARY_MEMORY: "balanced",
                },
                "subentry_type": "conversation",
                "title": "Chaos conversation",
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
    expected = ExpectedEffects()
    effects = []

    async def observe(call):
        effects.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("chaos_probe", "record", observe)
    hass.services.async_register("light", "turn_off", observe)
    client = await _admin_client(hass, hass_ws_client)

    async def save(config):
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        await _management_call(
            client,
            entry=entry,
            section="configuration",
            action="update",
            revision=before["revision"],
            config=config,
        )

    checkpoints: list[tuple[dict, ExpectedEffects]] = []
    turns = 0
    conversations: dict[str, str] = {}

    async def managers():
        return (
            await async_get_memory(hass, entry.entry_id, subentry.subentry_id),
            await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id),
            await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id),
        )

    for step in range(90 * stress_scale):
        memory, knowledge, rules = await managers()
        operation = rng.choices(
            (
                "memory_add",
                "memory_delete",
                "knowledge_create",
                "knowledge_delete",
                "rule_create",
                "rule_delete",
                "temporary_add",
                "temporary_delete",
                "guest_toggle",
                "config_edit",
                "exposure_toggle",
                "checkpoint",
                "restore",
                "reload",
                "conversation_turn",
                "cancel_turn",
                "tool_toggle",
            ),
            weights=(16, 6, 10, 5, 8, 4, 8, 3, 8, 4, 4, 7, 4, 5, 10, 3, 4),
            k=1,
        )[0]
        record(
            stress_trace,
            "sequence_choice",
            seed=stress_seed,
            step=step,
            choice=operation,
        )
        users = [f"chaos-user-{number}" for number in range(4)]
        if operation == "memory_add":
            user = rng.choice(users)
            record(stress_trace, operation, step=step, user=user)
            added = await memory.async_add(
                user,
                f"chaos marker {user} operation {step}",
                "acceptance",
                "explicit",
                key=f"chaos-{step}",
            )
            expected.memories.setdefault(user, {})[added["memory"]["memory_id"]] = (
                f"chaos marker {user} operation {step}"
            )
        elif operation == "memory_delete":
            user = rng.choice(users)
            if expected.memories.get(user):
                selected = rng.choice(list(expected.memories[user]))
                record(stress_trace, operation, step=step, user=user, id=selected)
                assert await memory.async_delete(user, [selected]) == 1
                del expected.memories[user][selected]
        elif operation == "knowledge_create":
            record(stress_trace, operation, step=step)
            created = await knowledge.async_create(
                f"Chaos title {step % 3}",
                "valid description",
                f"Knowledge marker {step} 東京",
            )
            expected.knowledge[created.source_id] = f"Knowledge marker {step} 東京"
        elif operation == "knowledge_delete":
            if expected.knowledge:
                selected = rng.choice(list(expected.knowledge))
                record(stress_trace, operation, step=step, id=selected)
                assert await knowledge.async_delete(selected)
                del expected.knowledge[selected]
        elif operation == "rule_create":
            record(stress_trace, operation, step=step)
            created = await rules.async_create(
                {
                    "name": f"Chaos rule {step}",
                    "phrases": [f"local command {step}"],
                    "match_type": "equals",
                    "action_type": "local_action",
                    "action": {
                        "actions": [
                            {
                                "action": "chaos_probe.record",
                                "data": {"message": f"rule effect {step}"},
                            }
                        ],
                        "success_response": f"Local effect {step}",
                    },
                }
            )
            expected.rules[created["id"]] = (
                f"Chaos rule {step}",
                f"local command {step}",
                f"rule effect {step}",
            )
        elif operation == "rule_delete":
            if expected.rules:
                selected = rng.choice(list(expected.rules))
                record(stress_trace, operation, step=step, id=selected)
                assert await rules.async_delete(selected)
                del expected.rules[selected]
        elif operation == "temporary_add":
            temporary = await async_get_temporary_memory(
                hass, entry.entry_id, subentry.subentry_id
            )
            user = rng.choice(users)
            added = await temporary.async_add(
                f"user:{user}",
                f"Temporary chaos marker {step}",
                (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
                "acceptance",
                owner_scope_id=f"user:{user}",
            )
            expected.temporary.setdefault(user, {})[added["memory"]["memory_id"]] = (
                f"Temporary chaos marker {step}"
            )
            record(stress_trace, operation, step=step, user=user)
        elif operation == "temporary_delete":
            temporary = await async_get_temporary_memory(
                hass, entry.entry_id, subentry.subentry_id
            )
            user = rng.choice(users)
            if expected.temporary.get(user):
                selected = rng.choice(list(expected.temporary[user]))
                await temporary.async_delete(
                    f"user:{user}", [selected], owner_scope_id=f"user:{user}"
                )
                del expected.temporary[user][selected]
                record(stress_trace, operation, step=step, user=user)
        elif operation == "guest_toggle":
            guest = await async_get_guest_mode(
                hass, entry.entry_id, subentry.subentry_id
            )
            assert guest is not None
            if expected.guest_active:
                await guest.async_disable_trusted()
            else:
                await guest.async_update_trusted(indefinite=True)
            expected.guest_active = not expected.guest_active
            assert guest.is_active() is expected.guest_active
            record(stress_trace, operation, step=step, active=expected.guest_active)
        elif operation == "config_edit":
            expected.max_tokens = 600 + step
            await save({"max_tokens": expected.max_tokens})
            record(stress_trace, operation, step=step, max_tokens=expected.max_tokens)
        elif operation == "tool_toggle":
            expected.tool_enabled = not expected.tool_enabled
            tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
            tool["enabled"] = expected.tool_enabled
            await save({CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False)})
            conversations.clear()
            record(stress_trace, operation, step=step, enabled=expected.tool_enabled)
        elif operation == "exposure_toggle":
            entity_id = "light.chaos_probe"
            exposed = bool(step % 2)
            if exposed:
                hass.states.async_set(entity_id, "on")
            else:
                hass.states.async_remove(entity_id)
            async_expose_entity(hass, conversation.DOMAIN, entity_id, exposed)
            expected.exposed = exposed
            record(stress_trace, operation, step=step, exposed=exposed)
        elif operation == "checkpoint":
            checkpoints.append(
                (
                    await backup.async_collect_backup_snapshot(hass, entry, subentry),
                    deepcopy(expected),
                )
            )
            record(stress_trace, operation, step=step, checkpoint=len(checkpoints) - 1)
        elif operation == "restore" and checkpoints:
            checkpoint, restored_expected = rng.choice(checkpoints)
            record(
                stress_trace,
                operation,
                step=step,
                checkpoint=next(
                    index
                    for index, item in enumerate(checkpoints)
                    if item[0] is checkpoint
                ),
            )
            assert (
                await backup.async_restore_backup(hass, entry, subentry, checkpoint)
            )["status"] == "restored"
            await hass.async_block_till_done()
            assert _semantic(
                await backup.async_collect_backup_snapshot(hass, entry, subentry)
            ) == _semantic(checkpoint)
            restored_expected = deepcopy(restored_expected)
            restored_expected.exposed = expected.exposed
            restored_expected.effects = expected.effects
            expected = restored_expected
            conversations.clear()
        elif operation == "reload":
            record(stress_trace, operation, step=step)
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            conversations.clear()

        subentry = entry.subentries[subentry.subentry_id]
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        temporary = await async_get_temporary_memory(
            hass, entry.entry_id, subentry.subentry_id
        )
        await expected.assert_stores(memory, knowledge, rules, temporary, users)
        guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        assert guest.is_active() is expected.guest_active

        async def probe(user, text, conversation_id=None, expected=expected, step=step):
            current = conversation.async_get_agent(hass, entry.entry_id)
            calls = []
            if not expected.guest_active:
                calls.append(("memory_list", {"scope": "personal", "limit": 100}))
                if expected.knowledge:
                    calls.append(
                        ("knowledge_get", {"source_id": next(iter(expected.knowledge))})
                    )
                if expected.tool_enabled and expected.exposed:
                    calls.append(
                        (
                            "execute_services",
                            {
                                "list": [
                                    {
                                        "domain": "light",
                                        "service": "turn_off",
                                        "service_data": {
                                            "entity_id": ["light.chaos_probe"]
                                        },
                                    }
                                ]
                            },
                        )
                    )
            replies = [
                _chat_sse_tool_call(
                    call_id=f"call-chaos-{uuid4().hex}", name=name, arguments=arguments
                )
                for name, arguments in calls
            ]
            replies.append(_chat_sse_text("chaos healthy"))
            wire = _install_wire(monkeypatch, current, replies)
            original_send = wire.send

            async def validate_send(request, *args, **kwargs):
                body = json.loads(request.content)
                _assert_valid_outgoing_history(body, "chat_completions")
                assert body["model"] == "gpt-5.6"
                assert body["max_completion_tokens"] == expected.max_tokens
                serialized = json.dumps(body)
                for owner, values in expected.memories.items():
                    if owner != user or expected.guest_active:
                        for content in values.values():
                            assert content not in serialized
                for owner, values in expected.temporary.items():
                    if owner != user or expected.guest_active:
                        for content in values.values():
                            assert content not in serialized
                return await original_send(request, *args, **kwargs)

            monkeypatch.setattr(_raw_client(current)._client, "send", validate_send)
            baseline = len(effects)
            result = await conversation.async_converse(
                hass=hass,
                text=text,
                conversation_id=conversation_id,
                context=Context(user_id=user),
                language="en",
                agent_id=entry.entry_id,
            )
            assert result.response.error_code is None
            assert (
                result.response.as_dict()["speech"]["plain"]["speech"]
                == "chaos healthy"
            )
            assert len(wire.requests) == len(calls) + 1
            names = {
                tool["function"]["name"]
                for tool in wire.requests[0]["body"].get("tools", [])
            }
            if not expected.tool_enabled:
                assert "execute_services" not in names
            elif expected.exposed and not expected.guest_active:
                assert "execute_services" in names
            for index, (name, arguments) in enumerate(calls):
                output = last_tool_result(
                    wire.requests[index + 1]["body"], "chat_completions"
                )
                if name == "memory_list":
                    assert {
                        item["memory_id"]: item["content"]
                        for item in output["memories"]
                    } == expected.memories.get(user, {})
                elif name == "knowledge_get":
                    assert expected.knowledge[arguments["source_id"]] in json.dumps(
                        output, ensure_ascii=False
                    )
            expected_new = (
                [("light", "turn_off", {"entity_id": ["light.chaos_probe"]})]
                if any(name == "execute_services" for name, _ in calls)
                else []
            )
            assert effects[baseline:] == expected_new
            expected.effects.extend(expected_new)
            assert effects == expected.effects
            if expected.guest_active:
                names = {
                    tool["function"]["name"]
                    for tool in wire.requests[0]["body"].get("tools", [])
                }
                assert not names.intersection(
                    {"execute_services", "memory_list", "knowledge_get"}
                )
            elif expected.rules:
                name, phrase, marker = next(iter(expected.rules.values()))
                local_wire = _install_wire(monkeypatch, current, [])
                baseline = len(effects)
                local = await conversation.async_converse(
                    hass=hass,
                    text=phrase,
                    conversation_id=None,
                    context=Context(user_id=user),
                    language="en",
                    agent_id=entry.entry_id,
                )
                assert local.response.error_code is None
                assert local.response.as_dict()["speech"]["plain"][
                    "speech"
                ] == "Local effect " + marker.removeprefix("rule effect ")
                expected_new = [("chaos_probe", "record", {"message": marker})]
                assert effects[baseline:] == expected_new
                expected.effects.extend(expected_new)
                assert effects == expected.effects
                assert local_wire.requests == []
            record(
                stress_trace,
                "behaviour_probe",
                step=step,
                user=user,
                guest=expected.guest_active,
                provider_requests=len(wire.requests),
                effect_count=len(expected.effects),
            )
            return result

        if operation == "cancel_turn":
            entered, release = asyncio.Event(), asyncio.Event()
            wire = _install_wire(
                monkeypatch, agent, [_chat_sse_text("cancelled reply")]
            )
            original_send = wire.send

            async def blocked_send(
                request,
                *args,
                _entered=entered,
                _release=release,
                _send=original_send,
                **kwargs,
            ):
                _entered.set()
                await _release.wait()
                return await _send(request, *args, **kwargs)

            monkeypatch.setattr(_raw_client(agent)._client, "send", blocked_send)
            task = asyncio.create_task(
                conversation.async_converse(
                    hass=hass,
                    text=f"cancel-chaos-{step}",
                    conversation_id=None,
                    context=Context(user_id=users[step % len(users)]),
                    language="en",
                    agent_id=entry.entry_id,
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            assert effects == expected.effects
            record(stress_trace, operation, step=step)
        user = (
            rng.choice(users)
            if operation == "conversation_turn"
            else users[step % len(users)]
        )
        result = await probe(
            user, f"private-chaos-{user}-turn-{step}", conversations.get(user)
        )
        conversations[user] = result.conversation_id
        if operation in {"config_edit", "tool_toggle"}:
            # The first probe checks the live save; a second checks the same effects after reload.
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            await probe(user, f"reloaded-chaos-{user}-turn-{step}")
            await expected.assert_stores(*(await managers()), temporary, users)
            conversations.clear()
        await assert_enhanced_health(
            hass,
            entry,
            subentry,
            HealthChecks(
                backup=True,
                memory_users=tuple(users),
                knowledge=True,
                request_rules=True,
                public_probe=False,
                probe_user=users[step % len(users)],
                probe_text=f"probe {step}",
                expected_speech="chaos healthy",
            ),
        )
        turns += 1
    record(
        stress_trace,
        "summary",
        chaos_operations=90 * stress_scale,
        checkpoints=len(checkpoints),
        public_conversation_turns=turns,
    )
