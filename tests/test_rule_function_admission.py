"""Rule admission cannot outlive the Function configuration it validated."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
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


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("mutation", ["delete", "schema"])
@pytest.mark.parametrize("checkpoint", ["conditions", "functions"])
async def test_rule_rejects_changed_function_baseline_before_write_and_next_save(
    function_agent,
    monkeypatch,
    action,
    nested,
    mutation,
    checkpoint,
):
    hass, entry, subentry, rules, store = function_agent
    await rules.async_delete("invoke-probe")
    existing = local_rule("Candidate")
    if action == "update":
        await rules.async_create(existing)
    before = await rules.async_backup_data()
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["parameters"] = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
    }
    prepared = await send(
        function_agent, "save", original_name="native_probe", tool=tool
    )
    prepared.send_error.assert_not_called()
    call = {
        "action": "extended_openai_conversation_responses.call_function",
        "data": {"function": "native_probe", "arguments": {"count": 1}},
    }
    candidate = deepcopy(existing)
    candidate["action"]["actions"] = [{"sequence": [call]}] if nested else [call]
    boundary = (
        "_validate_request_rule_conditions"
        if checkpoint == "conditions"
        else "async_validate_request_rule_functions"
    )
    validate = getattr(ui, boundary)
    changed = False

    async def change_during_validation(*args, **kwargs):
        nonlocal changed
        await validate(*args, **kwargs)
        if changed:
            return
        changed = True
        if mutation == "delete":
            result = await send(
                function_agent, "delete", name="native_probe", confirm=True
            )
        else:
            updated = deepcopy(tool)
            updated["spec"]["parameters"]["properties"]["count"]["minimum"] = 2
            result = await send(
                function_agent, "save", original_name="native_probe", tool=updated
            )
        result.send_error.assert_not_called()

    monkeypatch.setattr(ui, boundary, change_during_validation)
    request = {
        "section": "request_rules",
        "action": action,
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "revision": rules.revision(),
        "rule": candidate,
    }
    if action == "update":
        request["rule_id"] = existing["id"]
    from inspect import unwrap
    from types import SimpleNamespace
    from unittest.mock import Mock

    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    await unwrap(ui.websocket_management)(hass, connection, {"id": 1, **request})
    fresh = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == before, (
        "A Rule committed against an obsolete Function baseline"
    )
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "invalid_request"
    monkeypatch.setattr(ui, boundary, validate)
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=fresh))
    if mutation == "delete":
        restored = await send(function_agent, "save", tool=tool)
        restored.send_error.assert_not_called()
    else:
        call["data"]["arguments"]["count"] = 2
        candidate["action"]["actions"] = [{"sequence": [call]}] if nested else [call]
    request.update(revision=fresh.revision(), rule=candidate)
    saved = await ui.async_management_command(hass, "admin", True, request)
    assert saved["rule"]["id"] == existing["id"]
    reloaded = RequestRules(RequestRuleStore(hass, STORAGE_VERSION, store.key))
    await reloaded.async_initialize()
    assert len(reloaded.snapshot()["rules"]) == 1
    assert (
        reloaded.rule_snapshot(existing["id"])["action"]["actions"]
        == saved["rule"]["action"]["actions"]
    )
