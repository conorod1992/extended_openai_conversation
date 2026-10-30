"""Queued Rules retain validated Function ownership through reconciliation."""

import asyncio
from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    function_dependency_integrity as integrity,
    management_ui as ui,
    request_rules as rule_module,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION,
    RequestRules,
    RequestRuleStore,
)
from tests.test_function_acknowledgement_recovery import (
    function_agent as function_agent,
    send,
)
from tests.test_request_rules import local_rule


def function_action(name, count):
    return {
        "action": "extended_openai_conversation_responses.call_function",
        "data": {"function": name, "arguments": {"count": count}},
    }


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("mutation", ["delete", "schema", "unchanged"])
@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_queued_rule_admission_after_failed_write_and_function_validation(
    function_agent, monkeypatch, action, mutation, cancel_waiter
):
    hass, entry, subentry, rules, store = function_agent
    # Use the real factory, including its initialization/write lock. A getter
    # stub returning the manager immediately conceals important serialization.
    hass.data.setdefault(rule_module._MANAGERS, {})[
        entry.entry_id, subentry.subentry_id
    ] = rules
    monkeypatch.setattr(
        ui, "async_get_request_rules", rule_module.async_get_request_rules
    )
    await rules.async_delete("invoke-probe")
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
    }
    survivor = deepcopy(tool)
    survivor["spec"]["name"] = "survivor"
    for item, extra in ((tool, {"original_name": "native_probe"}), (survivor, {})):
        seeded = await send(function_agent, "save", tool=item, **extra)
        seeded.send_error.assert_not_called()
    unrelated = local_rule("Unrelated")
    unrelated["action"]["actions"] = [function_action("survivor", 1)]
    await rules.async_create(unrelated)
    candidate = local_rule("Queued rule")
    if action == "update":
        await rules.async_create(candidate)
    before = await rules.async_backup_data()
    listed = await send(function_agent, "list", section="request_rules")
    listed.send_error.assert_not_called()
    revision = listed.send_result.call_args.args[1]["revision"]
    candidate["action"]["actions"] = [
        {"sequence": [function_action("native_probe", 1)]}
    ]
    rule_validating, release_rule, validated, function_validating, release_function = (
        asyncio.Event() for _ in range(5)
    )
    writing, release_write = (asyncio.Event() for _ in range(2))
    validate_arguments = integrity.async_validate_static_function_arguments
    validate_rule = ui._async_validate_request_rule_mutation
    reconcile = rules._async_reconcile_failed_save
    save = store.async_save
    queued = function = None
    writes = 0

    async def delayed_arguments(*args, **kwargs):
        await validate_arguments(*args, **kwargs)
        if asyncio.current_task() is queued:
            rule_validating.set()
            await release_rule.wait()
        elif asyncio.current_task() is function:
            function_validating.set()
            await release_function.wait()

    async def observed_rule(*args):
        result = await validate_rule(*args)
        validated.set()
        return result

    async def failed_save(data):
        nonlocal writes
        writes += 1
        if writes == 1:
            writing.set()
            await release_write.wait()
            raise OSError("write failed before replacement")
        await save(data)

    async def observed_reconciliation():
        await reconcile()
        # Static Function validation can finish while the real reconciliation
        # task has restored the old revision, before its lock owner resumes.
        release_function.set()

    monkeypatch.setattr(
        integrity, "async_validate_static_function_arguments", delayed_arguments
    )
    monkeypatch.setattr(ui, "_async_validate_request_rule_mutation", observed_rule)
    monkeypatch.setattr(store, "async_save", failed_save)
    monkeypatch.setattr(rules, "_async_reconcile_failed_save", observed_reconciliation)
    tasks = []
    try:
        queued = asyncio.create_task(
            send(
                function_agent,
                action,
                section="request_rules",
                rule=candidate,
                revision=revision,
                **({"rule_id": candidate["id"]} if action == "update" else {}),
            )
        )
        tasks.append(queued)
        await rule_validating.wait()
        if mutation != "unchanged":
            updated = deepcopy(tool)
            updated["spec"]["parameters"]["properties"]["count"]["minimum"] = 2
            function = asyncio.create_task(
                send(function_agent, "delete", name="native_probe", confirm=True)
                if mutation == "delete"
                else send(
                    function_agent, "save", original_name="native_probe", tool=updated
                )
            )
            tasks.append(function)
            await function_validating.wait()
        settings = asyncio.create_task(
            send(
                function_agent,
                "settings",
                section="request_rules",
                revision=revision,
                defaults=before["defaults"],
                wording_groups=before["wording_groups"],
            )
        )
        tasks.append(settings)
        await writing.wait()
        release_rule.set()
        await validated.wait()
        assert not queued.done()
        if cancel_waiter:
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
        release_write.set()
        failed = await settings
        assert failed.send_error.call_args.args[1] == "eoai_mutation_uncertain"
        if function is not None:
            changed = await function
            changed.send_error.assert_not_called()
        if not cancel_waiter:
            response = await queued
        fresh = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
        await fresh.async_initialize()
        if cancel_waiter or mutation != "unchanged":
            assert await fresh.async_backup_data() == before
            assert writes == 1, "Rejected/cancelled Rules must not dispatch a save"
            if not cancel_waiter:
                response.send_result.assert_not_called()
                assert response.send_error.call_args.args[1] == "invalid_request"
        else:
            response.send_error.assert_not_called()
            assert (
                fresh.rule_snapshot(candidate["id"])["action"]["actions"]
                == candidate["action"]["actions"]
            )
            assert writes == 2
        if mutation == "delete":
            restored = await send(function_agent, "save", tool=tool)
            restored.send_error.assert_not_called()
        elif mutation == "schema":
            candidate["action"]["actions"][0]["sequence"][0]["data"]["arguments"][
                "count"
            ] = 2
        next_action = (
            "update"
            if action == "update" or (mutation == "unchanged" and not cancel_waiter)
            else "create"
        )
        saved = await send(
            function_agent,
            next_action,
            section="request_rules",
            rule=candidate,
            revision=rules.revision(),
            **({"rule_id": candidate["id"]} if next_action == "update" else {}),
        )
        saved.send_error.assert_not_called()
        final = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
        await final.async_initialize()
        assert len(final.snapshot()["rules"]) == 2
        assert (
            final.rule_snapshot(candidate["id"])["action"]["actions"]
            == candidate["action"]["actions"]
        )
        assert (
            final.rule_snapshot(unrelated["id"])["action"]["actions"]
            == unrelated["action"]["actions"]
        )
    finally:
        for event in (release_rule, release_function, release_write):
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
