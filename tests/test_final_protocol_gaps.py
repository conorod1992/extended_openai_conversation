"""Final meaningful request protocol, diagnostics, lifecycle and preview contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
from openai import AuthenticationError, OpenAIError
import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    agent_test,
    debug,
    debug_management_projection as projection,
    helpers,
    intercom,
    management_ui as ui,
    request_rule_match_preview as preview,
    restore_recovery,
    tool_exchange,
    transfer,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_agent_test import _objects
from tests.test_request_rules import local_rule, manager
from tests.test_transfer_user_mapping import _prepared


@pytest.mark.parametrize("error_kind", ["authentication", "provider"])
async def test_agent_probe_failure_without_function_tools_keeps_function_check_skipped(
    monkeypatch, error_kind
):
    hass, entry, subentry, client, usage = _objects()
    subentry.subentry_type = "ai_task_data"
    if error_kind == "authentication":
        error = AuthenticationError(
            "rejected",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://provider.test")
            ),
            body=None,
        )
    else:
        error = OpenAIError("probe failed")
    client.chat.completions.create.side_effect = error
    monkeypatch.setattr(agent_test, "get_exposed_entities", lambda *_: [])
    monkeypatch.setattr(agent_test, "async_get_usage", AsyncMock(return_value=usage))
    reauth = Mock(return_value=True)
    monkeypatch.setattr(agent_test, "request_reauthentication", reauth)
    result = await agent_test.async_test_agent(hass, entry, subentry)
    checks = [check for check in result.checks if check.name == "Function calling"]
    assert len(checks) == 1 and checks[0].status == "Skipped"
    assert result.status == "Failed"
    assert result.authentication_rejected == (error_kind == "authentication")
    usage.async_record_request.assert_awaited_once()
    assert usage.async_record_request.call_args.kwargs["successful"] is False
    assert "tools" not in client.chat.completions.create.call_args.kwargs


@pytest.mark.parametrize("awaitable", [False, True])
async def test_authentication_accepts_empty_model_page_on_both_sdk_shapes(
    hass, monkeypatch, awaitable
):
    iterations = []

    class Page:
        async def __aiter__(self):
            iterations.append("iterated")
            if False:
                yield None

    page = Page()

    async def get_page():
        return page

    listing = Mock(side_effect=lambda **_kwargs: get_page() if awaitable else page)
    client = SimpleNamespace(models=SimpleNamespace(list=listing))
    monkeypatch.setattr(helpers, "AsyncOpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(helpers, "get_async_client", lambda *_: object())
    result = await helpers.get_authenticated_client(hass, "key", None, None, None, None)
    assert result is client and iterations == ["iterated"]
    listing.assert_called_once_with(timeout=10)


@pytest.mark.parametrize("error_type", [None, "LocalIntentError"])
def test_debug_trace_normal_completion_records_result_and_resets_context(
    hass, monkeypatch, error_type
):
    manager = debug.DebugManager()
    manager.configure(enabled=True, limit=5)
    monkeypatch.setattr(debug, "get_debug_manager", lambda *_: manager)
    agent = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    previous = debug._ACTIVE_DEBUG_TRACE.get()
    with debug.conversation_debug_trace(
        agent, SimpleNamespace(text="Hello", conversation_id="conversation")
    ) as trace:
        assert debug.current_debug_trace() is trace
        trace.result = {"speech": "Completed"}
        trace.error_type = error_type
    captured = manager.get(trace.debug_id)
    assert captured["result"] == {"speech": "Completed"}
    assert captured["successful"] == (error_type is None)
    assert debug._ACTIVE_DEBUG_TRACE.get() is previous


def test_debug_projection_marks_truncated_diagnostic_metadata():
    manager = debug.DebugManager()
    manager.configure(enabled=True, limit=5)
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={"text": "hello"},
        incoming_conversation_id="conversation",
    )
    trace.memory["_payload_preparation"] = {"oversized": "x" * 100000}
    manager.finish(trace, successful=True)
    page = projection.debug_trace_page(manager, trace.debug_id)
    assert page["payload_latency_diagnostics"]["metadata_truncated"]
    assert page["management_projection"]["fields"]["payload_latency_diagnostics"][
        "truncated"
    ]
    assert len(str(page["payload_latency_diagnostics"])) < 10000
    assert trace.memory["_payload_preparation"]["oversized"] == "x" * 100000


@pytest.mark.parametrize(
    "extra_call,text",
    [(False, "Keep this explanation"), (True, ""), (True, "Keep this explanation")],
)
async def test_repeated_tool_cleanup_preserves_text_and_unrelated_call(
    extra_call, text
):
    old = SimpleNamespace(id="done", tool_name="demo", tool_args={}, external=True)
    duplicate = SimpleNamespace(
        id="done", tool_name="demo", tool_args={}, external=True
    )
    new = SimpleNamespace(id="new", tool_name="demo", tool_args={}, external=True)
    pending = [duplicate, new] if extra_call else [duplicate]
    original = conversation.AssistantContent(
        agent_id="agent", content="Original", tool_calls=[old]
    )
    result = conversation.ToolResultContent(
        agent_id="agent",
        tool_call_id="done",
        tool_name="demo",
        tool_result={"result": "ok"},
    )
    repeated = conversation.AssistantContent(
        agent_id="agent", content=text, tool_calls=list(pending)
    )
    tail = conversation.UserContent(content="later")
    log = SimpleNamespace(content=[original, result, repeated, tail])
    log.async_add_assistant_content_without_tools = lambda item: log.content.append(
        item
    )
    entity = SimpleNamespace(entity_id="agent", _execute_function_tool=AsyncMock())
    with pytest.raises(HomeAssistantError, match="repeated a completed tool call"):
        await tool_exchange.async_execute_tool_exchange(
            entity, log, pending, [], FunctionCallBudget(10), None, []
        )
    assert (
        original.tool_calls == [old]
        and original in log.content
        and result in log.content
    )
    assert repeated in log.content and repeated.content == text
    assert repeated.tool_calls == ([new] if extra_call else [])
    completed_results = [
        item for item in log.content if getattr(item, "tool_call_id", None) == "done"
    ]
    assert completed_results == [result]
    if extra_call:
        assert (
            len(
                [
                    item
                    for item in log.content
                    if getattr(item, "tool_call_id", None) == "new"
                ]
            )
            == 1
        )
    entity._execute_function_tool.assert_not_awaited()


@pytest.mark.parametrize("fails", [False, True])
async def test_restore_precondition_runs_under_exclusivity_before_materialization(
    hass, monkeypatch, fails
):
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        get_agent_maintenance_gate,
    )

    entry, subentry = (
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
    )
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    order = []

    async def precondition():
        assert gate.owns_exclusive()
        order.append("precondition")
        if fails:
            raise HomeAssistantError("stale preview")

    async def materialize(*_args, **_kwargs):
        assert order == ["precondition"] and gate.owns_exclusive()
        order.append("materialize")
        return "target", {"selected": True}

    materialize_mock = AsyncMock(side_effect=materialize)
    restore, reload = AsyncMock(return_value={"restored": True}), AsyncMock()
    monkeypatch.setattr(transfer, "async_materialize_restore", materialize_mock)
    monkeypatch.setattr(restore_recovery, "async_restore_backup_recoverably", restore)
    monkeypatch.setattr(restore_recovery, "async_finish_restore_reload", reload)
    if fails:
        with pytest.raises(HomeAssistantError, match="stale preview"):
            await transfer.async_restore_transfer(
                hass, entry, subentry, object(), precondition=precondition
            )
        materialize_mock.assert_not_awaited()
        restore.assert_not_awaited()
        reload.assert_not_awaited()
    else:
        result = await transfer.async_restore_transfer(
            hass, entry, subentry, object(), precondition=precondition
        )
        assert result == {"restored": True, "transfer": {"selected": True}}
        reload.assert_awaited_once()
    assert not gate.owns_exclusive()


async def test_transfer_ignores_mapping_for_existing_or_unrelated_owner(hass):
    prepared = _prepared()
    hass.auth.async_get_users = AsyncMock(
        return_value=[SimpleNamespace(id="source-a", name="Alice")]
    )
    plan = await transfer.async_user_scope_mapping_plan(
        hass,
        prepared,
        prepared.available_sections,
        {"source-a": "nonexistent", "unrelated": "nonexistent"},
    )
    assert (
        plan["required_source_user_ids"] == [] and plan["missing_source_user_ids"] == []
    )
    assert plan["resolved"] == {"source-a": "source-a"}


async def test_continuation_preview_without_eligible_match_has_no_actions(hass):
    rule = local_rule()
    rule["continue_matching"] = True
    rules = await manager(rule)
    assert rules._has_continuation
    result = await preview.async_request_rule_match_preview(
        hass, rules, "unrelated request"
    )
    assert result["matched"] is False
    assert result["matched_rules"] == [] and result["skipped_conditions"] == []
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "quiet,intercom_present", [(False, False), (True, False), (False, True)]
)
async def test_remove_final_entry_is_safe_with_partial_setup_and_repeated_calls(
    hass, monkeypatch, quiet, intercom_present
):
    delete = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_entry_data", delete)
    hass.config_entries.async_entries.return_value = []
    managers = []
    if quiet:
        manager = SimpleNamespace(async_shutdown=AsyncMock())
        hass.data[integration.DOMAIN] = {"quiet_hours_manager": manager}
        managers.append(manager)
    if intercom_present:
        manager = SimpleNamespace(async_shutdown=AsyncMock())
        hass.data[intercom.DATA_KEY] = manager
        managers.append(manager)
    entry = SimpleNamespace(entry_id="entry")
    await integration.async_remove_entry(hass, entry)
    await integration.async_remove_entry(hass, entry)
    for manager in managers:
        manager.async_shutdown.assert_awaited_once()
    assert hass.data[f"{integration.DOMAIN}.removed"] is True
    assert intercom.DATA_KEY not in hass.data
    assert "quiet_hours_manager" not in hass.data.get(integration.DOMAIN, {})
    assert hass.services.async_remove.call_count == 6


async def test_management_setup_retry_preserves_already_registered_panel(
    hass, monkeypatch
):
    assets, panel, websocket = AsyncMock(), AsyncMock(), Mock()
    monkeypatch.setattr(ui, "async_register_frontend_assets", assets)
    monkeypatch.setattr(ui.panel_custom, "async_register_panel", panel)
    monkeypatch.setattr(ui.websocket_api, "async_register_command", websocket)
    monkeypatch.setattr(ui, "frontend_entry_url", lambda *_: "/frontend.js")
    hass.data[f"{ui._UI_SETUP}.panel"] = True
    await ui.async_setup_management_ui(hass)
    await ui.async_setup_management_ui(hass)
    assets.assert_awaited_once()
    websocket.assert_called_once()
    panel.assert_not_awaited()
    assert hass.data[ui._UI_SETUP]


async def test_intercom_shutdown_is_idempotent_before_listener_registration(hass):
    manager = intercom.IntercomManager(hass)
    await manager.async_shutdown()
    await manager.async_shutdown()
    assert manager._closed and not manager._enabled and manager._unsub_state is None


@pytest.mark.parametrize("missing", ["device", "area", "all"])
def test_ambiguous_broadcast_alias_handles_missing_registry_associations(
    hass, monkeypatch, missing
):
    manager = intercom.IntercomManager(hass)
    satellite = {"id": "assist_satellite.portable", "name": "Portable", "labels": []}
    if missing != "all":
        satellite.update(device_id="device", area_id="area")
    catalog = {
        "satellites": [satellite],
        "devices": [],
        "areas": [],
        "floors": [],
        "labels": [
            {"id": "one", "name": "Kitchen", "aliases": []},
            {"id": "two", "name": "Kitchen", "aliases": []},
        ],
    }
    entity = SimpleNamespace(labels={"one", "two"})
    monkeypatch.setattr(manager, "catalog", lambda: catalog)
    monkeypatch.setattr(
        intercom.er,
        "async_get",
        lambda *_: SimpleNamespace(async_get=lambda *_: entity),
    )
    monkeypatch.setattr(
        intercom.dr,
        "async_get",
        lambda *_: SimpleNamespace(
            async_get=lambda *_: (
                None if missing in {"device", "all"} else SimpleNamespace(labels=set())
            )
        ),
    )
    monkeypatch.setattr(
        intercom.ar,
        "async_get",
        lambda *_: SimpleNamespace(
            async_get_area=lambda *_: (
                None
                if missing in {"area", "all"}
                else SimpleNamespace(labels=set(), floor_id=None)
            )
        ),
    )
    assert manager.resolve_named_target("Kitchen") == {
        "label_ids": ["one"],
        "name": "Kitchen",
    }
