"""A retained Rule manager cannot silently fall through to AI after invalidation."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from homeassistant.core import HomeAssistant
from tests.test_configuration_lifecycle_hardening import _disabled_runtime_config
from tests.test_conversation_orchestration import _assist_fixture
from tests.test_request_rules import local_rule


@pytest.fixture
async def real_hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=MockUser(id="alice", is_owner=True))
    )
    hass.config_entries = SimpleNamespace(async_get_entry=lambda _: None)
    try:
        yield hass
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("after_write", [False, True], ids=["not_written", "written"])
@pytest.mark.parametrize("invalidation_point", ["before_turn", "before_matching"])
@pytest.mark.parametrize(
    "cancel_recovery", [False, True], ids=["ordinary", "cancelled"]
)
async def test_running_conversation_recovers_rules_without_ai_fallthrough(
    real_hass, monkeypatch, caplog, after_write, invalidation_point, cancel_recovery
):
    hass = real_hass
    entity, invoke, _logs, provider_requests = _assist_fixture(monkeypatch)
    entity.hass = hass
    entity.subentry.data = {**_disabled_runtime_config(), "functions": "[]"}
    entity._extended_openai_runtime_config_data = entity.subentry.data
    effects = []

    async def record(call):
        effects.append(call.data["version"])

    hass.services.async_register("rule_probe", "record", record)
    rules = await rr.async_get_request_rules(hass, "entry", "agent")
    entity._request_rules = rules
    entity._request_rule_runtime = rr.get_request_rule_runtime(hass, "entry", "agent")
    rule = local_rule("Retained")
    rule["action"]["actions"] = [
        {"action": "rule_probe.record", "data": {"version": "old"}}
    ]
    rule["action"]["success_response"] = "Known reply"
    created = await rules.async_create(rule)
    first, _ = await invoke("good night", direct=True)
    assert first.response.speech["plain"]["speech"] == "Known reply"
    assert effects == ["old"] and provider_requests == []
    write, load = rules._store._write_prepared_data, rules._store.async_load

    def failed_write(*args):
        if after_write:
            write(*args)
        raise OSError("PRIVATE-RULE-WRITE")

    monkeypatch.setattr(rules._store, "_write_prepared_data", failed_write)
    monkeypatch.setattr(
        rules._store, "async_load", AsyncMock(side_effect=OSError("PRIVATE-RULE-READ"))
    )
    updated = deepcopy(created)
    updated["action"]["actions"][0]["data"]["version"] = "submitted"
    updated["action"]["success_response"] = "Submitted reply"

    async def invalidate():
        with pytest.raises(OSError):
            await rules.async_update(
                created["id"], updated, expected_revision=rules.revision()
            )

    if invalidation_point == "before_turn":
        await invalidate()
    else:
        evaluate = conversation_module.async_evaluate_rule

        async def invalidate_before_matching(*args, **kwargs):
            # The failed writer settles after the request's recovery check but
            # before its matcher starts. Evaluation must still reject unknown state.
            await invalidate()
            monkeypatch.setattr(conversation_module, "async_evaluate_rule", evaluate)
            return await evaluate(*args, **kwargs)

        monkeypatch.setattr(
            conversation_module, "async_evaluate_rule", invalidate_before_matching
        )
    unavailable, _ = await invoke("good night", direct=True)
    assert "unavailable" in unavailable.response.speech["plain"]["speech"]
    assert effects == ["old"] and provider_requests == []
    monkeypatch.setattr(rules._store, "_write_prepared_data", write)
    reads = AsyncMock(wraps=load)
    monkeypatch.setattr(rules._store, "async_load", reads)
    fresh = rr.RequestRules(
        rr.RequestRuleStore(hass, rr.STORAGE_VERSION, rules._store.key)
    )
    await fresh.async_initialize()
    expected = "submitted" if after_write else "old"
    assert (
        fresh.rule_snapshot(created["id"])["action"]["actions"][0]["data"]["version"]
        == expected
    )
    if cancel_recovery:
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_load():
            value = await load()
            entered.set()
            await release.wait()
            return value

        monkeypatch.setattr(rules._store, "async_load", delayed_load)
        recovering = asyncio.create_task(invoke("good night", direct=True))
        try:
            await entered.wait()
            recovering.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recovering
        finally:
            release.set()
            await asyncio.gather(recovering, return_exceptions=True)
        assert effects == ["old"] and provider_requests == []
        async with asyncio.timeout(2):
            async with get_agent_maintenance_gate(hass, "entry", "agent").exclusive():
                pass
        monkeypatch.setattr(rules._store, "async_load", reads)
    recovered, _ = await invoke("good night", direct=True)
    assert recovered.response.speech["plain"]["speech"] == (
        "Submitted reply" if after_write else "Known reply"
    )
    assert effects == ["old", expected] and provider_requests == []
    await invoke("good night", direct=True)
    reads.assert_awaited_once()
    following = fresh.rule_snapshot(created["id"])
    following["action"]["success_response"] = "Next saved reply"
    await rules.async_update(
        created["id"], following, expected_revision=rules.revision()
    )
    next_result, _ = await invoke("good night", direct=True)
    assert next_result.response.speech["plain"]["speech"] == "Next saved reply"
    final = rr.RequestRules(
        rr.RequestRuleStore(hass, rr.STORAGE_VERSION, rules._store.key)
    )
    await final.async_initialize()
    assert (
        final.rule_snapshot(created["id"])["action"]["success_response"]
        == "Next saved reply"
    )
    assert effects == ["old", expected, expected, expected] and provider_requests == []
    assert "PRIVATE-RULE" not in caplog.text


@pytest.mark.parametrize("continuation", [False, True])
@pytest.mark.parametrize("outcome", ["stable", "committed", "not_written", "written"])
async def test_matching_budget_fallback_requires_current_rule_generation(
    real_hass, monkeypatch, continuation, outcome
):
    hass = real_hass
    entity, invoke, _logs, provider_calls = _assist_fixture(monkeypatch)
    entity.hass = hass
    entity.subentry.data = {**_disabled_runtime_config(), "functions": "[]"}
    entity._extended_openai_runtime_config_data = entity.subentry.data
    effects = []

    async def record(call):
        effects.append(call.data["version"])

    hass.services.async_register("budget_probe", "record", record)
    rules = await rr.async_get_request_rules(hass, "entry", "agent")
    entity._request_rules = rules
    entity._request_rule_runtime = rr.get_request_rule_runtime(hass, "entry", "agent")
    rule = local_rule("Retained", ["good {time}"], match_type="sentence_pattern")
    rule["continue_matching"] = continuation
    rule["action"]["actions"] = [
        {"action": "budget_probe.record", "data": {"version": "old"}}
    ]
    created = await rules.async_create(rule)
    budget = rr.MatchBudget
    monkeypatch.setattr(rr, "MatchBudget", lambda: budget(maximum=0))
    started, release = asyncio.Event(), asyncio.Event()
    executor = hass.async_add_executor_job

    async def controlled_executor(func, *args):
        if isinstance(getattr(func, "__self__", None), rr._MatchCursor):
            started.set()
            await release.wait()
        return await executor(func, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", controlled_executor)
    request = asyncio.create_task(invoke("good night", direct=True))
    try:
        await started.wait()
        if outcome != "stable":
            updated = deepcopy(created)
            updated["action"]["actions"][0]["data"]["version"] = "new"
            if outcome == "committed":
                await rules.async_update(
                    created["id"], updated, expected_revision=rules.revision()
                )
            else:
                write, load = rules._store._write_prepared_data, rules._store.async_load

                def fail(*args):
                    if outcome == "written":
                        write(*args)
                    raise OSError("write failed")

                monkeypatch.setattr(rules._store, "_write_prepared_data", fail)
                monkeypatch.setattr(
                    rules._store,
                    "async_load",
                    AsyncMock(side_effect=OSError("read failed")),
                )
                with pytest.raises(OSError):
                    await rules.async_update(
                        created["id"], updated, expected_revision=rules.revision()
                    )
                monkeypatch.setattr(rules._store, "_write_prepared_data", write)
                monkeypatch.setattr(rules._store, "async_load", load)
        release.set()
        result, _ = await request
        if outcome == "stable":
            assert len(provider_calls) == 1
        else:
            assert provider_calls == []
            assert (
                "changed during matching" in result.response.speech["plain"]["speech"]
            )
        assert effects == []
        fresh = rr.RequestRules(
            rr.RequestRuleStore(hass, rr.STORAGE_VERSION, rules._store.key)
        )
        await fresh.async_initialize()
        expected = "new" if outcome in {"written", "committed"} else "old"
        assert (
            fresh.rule_snapshot(created["id"])["action"]["actions"][0]["data"][
                "version"
            ]
            == expected
        )
        monkeypatch.setattr(rr, "MatchBudget", budget)
        following, _ = await invoke("good night", direct=True)
        assert effects == [expected]
        assert following.response.speech["plain"]["speech"] == "Done"
        assert len(provider_calls) == (1 if outcome == "stable" else 0)
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
