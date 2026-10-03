"""Management candidates cannot execute through Assist before Store settlement."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION,
    RequestRules,
    RequestRuleStore,
)
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_provider_wire_e2e import _agent, _chat_sse_text, _install_wire
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.parametrize(
    "mutation", ["create", "action", "enable", "disable", "update"]
)
@pytest.mark.parametrize(
    "outcome", ["success", "pre-replace", "post-replace", "cancel"]
)
@pytest.mark.usefixtures("real_store_io")
async def test_management_rule_generation_waits_for_authoritative_persistence(
    hass, monkeypatch, stress_trace, mutation, outcome
):
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    rules = agent._request_rules
    markers = []

    async def mark(call):
        markers.append(call.data["message"])

    hass.services.async_register("rule_probe", "record", mark)
    original = None
    if mutation != "create":
        original = await rules.async_create(_local([_record_action("OLD")]))
        if mutation == "enable":
            original = await rules.async_update(
                original["id"], {**original, "enabled": False}
            )
    candidate = deepcopy(original) if original else _local([_record_action("NEW")])
    if mutation == "action":
        candidate["action"]["actions"] = [_record_action("NEW")]
    elif mutation in {"enable", "disable"}:
        candidate["enabled"] = mutation == "enable"
    elif mutation == "update":
        candidate["phrases"] = ["changed rule"]
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("PROVIDER")] * 6)
    admin = await hass.auth.async_create_user(
        "Publication admin", group_ids=["system-admin"]
    )
    store = rules._store
    entered, release = asyncio.Event(), asyncio.Event()
    write = store._async_write_data
    replace = atomicwrites.replace_atomic
    replacements = []

    async def held_write(data):
        entered.set()
        await release.wait()
        await write(data)

    def faulted_replace(source, destination):
        if Path(destination) != Path(store.path):
            return replace(source, destination)
        replacements.append(outcome)
        if outcome == "pre-replace":
            raise OSError("held pre-replacement failure")
        result = replace(source, destination)
        if outcome == "post-replace":
            raise OSError("held post-replacement acknowledgement failure")
        return result

    monkeypatch.setattr(store, "_async_write_data", held_write)
    monkeypatch.setattr(atomicwrites, "replace_atomic", faulted_replace)
    message = {
        "section": "request_rules",
        "action": "create" if original is None else "update",
        "entry_id": agent.entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
        "rule": candidate,
        "revision": rules.revision(),
    }
    if original:
        message["rule_id"] = original["id"]
    task = asyncio.create_task(async_management_command(hass, admin.id, True, message))

    async def probe(text, expected):
        before_markers, before_wire = len(markers), len(wire.requests)
        result = await asyncio.wait_for(_say(hass, agent, text), 5)
        assert _speech(result) == ("Done" if expected else "PROVIDER")
        assert markers[before_markers:] == ([expected] if expected else [])
        assert len(wire.requests) - before_wire == int(expected is None)

    def expected_for(rule, text):
        if rule is None or not rule.get("enabled", True) or text not in rule["phrases"]:
            return None
        return rule["action"]["actions"][0]["data"]["message"]

    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert not task.done() and not replacements
        if outcome == "cancel":
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        # Both public requests complete while persistence is still held. These
        # assert actual HA service effects and SDK handoff, not matcher metadata.
        for text in ("run rule", "changed rule"):
            await probe(text, expected_for(original, text))
        assert not task.done() and not replacements
        release.set()
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"pre-replace", "post-replace"}:
            with pytest.raises(OSError):
                await task
        else:
            await task
        assert replacements == [outcome]
        authoritative = original if outcome == "pre-replace" else candidate
        for text in ("run rule", "changed rule"):
            await probe(text, expected_for(authoritative, text))
        fresh = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
        await fresh.async_initialize()
        for text in ("run rule", "changed rule"):
            match = fresh.match(text)
            expected = expected_for(authoritative, text)
            assert (match is None) is (expected is None)
            if match:
                assert match.rule["action"]["actions"][0]["data"]["message"] == expected
        disk = (
            json.loads(Path(store.path).read_text())["data"]["rules"]
            if Path(store.path).exists()
            else []
        )
        assert len(disk) == int(authoritative is not None)
        record(
            stress_trace,
            "summary",
            rule_publication_cases=1,
            mutation=mutation,
            outcome=outcome,
            layer="genuine-ha-store-assist",
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
