"""Exercise every remaining built-in Function type through public Assist and SDK wire."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from aiohttp import web
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions import (
    sqlite as sqlite_module,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)
from tests_stress.conftest import record

REMAINING_TYPES = (
    "rest",
    "scrape",
    "composite",
    "sqlite",
    "bash",
    "read_file",
    "write_file",
    "edit_file",
)
ERROR_CASES = (
    "rest_404",
    "scrape_missing_selector",
    "sqlite_bad_query",
    "bash_nonzero",
    "read_file_missing",
    "write_file_denied",
    "edit_file_no_match",
)

API_MODES = (API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES)


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize(
    "kind",
    (
        "template",
        "script",
        "rest",
        "scrape",
        "sqlite",
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "composite",
    ),
)
async def test_shared_backend_business_outcomes_survive_enclosing_workflows(
    hass, monkeypatch, socket_enabled, tmp_path, stress_trace, kind, api_mode
):
    """The same independent result contract applies to every applicable consumer."""
    from copy import deepcopy
    from html import escape

    import voluptuous as vol

    from custom_components.extended_openai_conversation_responses.const import (
        DOMAIN,
        SERVICE_CALL_FUNCTION,
    )
    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )
    from homeassistant.components import ai_task
    from homeassistant.core import SupportsResponse
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import llm
    from custom_components.extended_openai_conversation_responses.function_execution import (
        propagate_function_execution_errors,
    )
    from tests.functions.behaviour_generators import assert_typed_value
    from tests_real_ha.test_ai_task_provider_wire import _task_entity, _wire
    from tests_real_ha.test_ai_task_runtime import CallerAPI
    from tests_real_ha.test_cross_feature_acceptance import _say
    from tests_real_ha.test_request_rules_script_semantics import _local
    from tests_stress.function_outcome_contract import (
        BUSINESS_VALUES,
        backend_configuration,
        expected_consumer_result,
    )

    current = {"value": None}
    captured = []

    async def read(_call):
        if current.get("unavailable"):
            raise HomeAssistantError("backend dependency is unavailable")
        return {"payload": deepcopy(current["value"])}

    async def capture(call):
        captured.append(deepcopy(call.data["value"]))

    async def remote(request):
        if current.get("unavailable"):
            request.transport.close()
            return web.Response()
        serialized = json.dumps(current["value"])
        return web.Response(
            text=(
                f'<span class="probe">{escape(serialized)}</span>'
                if request.path == "/html"
                else serialized
            ),
            content_type="text/html" if request.path == "/html" else "application/json",
        )

    hass.services.async_register(
        "outcome_probe", "read", read, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register("outcome_probe", "capture", capture)
    app = web.Application()
    app.router.add_get("/value", remote)
    app.router.add_get("/html", remote)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    entries = []
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        config = backend_configuration(kind, tmp_path, url)
        backend = get_function(kind)
        validated = backend.validate_schema(config)
        with sqlite3.connect(tmp_path / "outcome.db") as db:
            db.execute("CREATE TABLE probes (value TEXT)")
            db.execute("INSERT INTO probes VALUES ('baseline')")
        tool = {
            "spec": {
                "name": "business_probe",
                "description": "Return actual business data",
                "parameters": {
                    "type": "object",
                    "properties": {"payload": {}, "payload_json": {"type": "string"}},
                },
            },
            "function": config,
        }
        entry = _make_entry(
            "Shared backend outcomes",
            include_ai_task=False,
            conversation_options={
                "chat_model": "gpt-5.2",
                "reasoning_effort": "none",
                "function_tool_error_recovery": True,
                CONF_API_MODE: api_mode,
                CONF_FUNCTION_TOOLS: [tool],
            },
        )
        await _setup_entry(hass, entry)
        entries.append(entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        task_entry, task_entity = await _task_entity(hass, api_mode)
        entries.append(task_entry)

        class BackendTool(llm.Tool):
            name = "business_probe"
            description = "Call the genuine configured backend"
            parameters = vol.Schema(
                {vol.Required("payload"): object, vol.Required("payload_json"): str}
            )

            async def async_call(self, hass, tool_input, llm_context):
                with propagate_function_execution_errors():
                    return {
                        "result": await backend.execute(
                            hass, validated, tool_input.tool_args, llm_context, []
                        )
                    }

        caller = CallerAPI(hass=hass, id=f"shared-{kind}", name="Shared outcome caller")
        caller.tools = [BackendTool()]
        from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
            caller_api_tools,
        )

        _, task_tools = caller_api_tools(
            await caller.async_get_api_instance(
                llm.LLMContext(
                    platform=DOMAIN,
                    context=Context(),
                    language="en",
                    assistant="ai_task",
                    device_id=None,
                )
            )
        )
        task_tool_name = task_tools[0]["spec"]["name"]
        observations = [(value, "business", False) for value in BUSINESS_VALUES]
        if kind in {"script", "rest", "sqlite", "read_file"}:
            observations += [
                (False, "healthy", False),
                (False, "injected_failure", True),
                (False, "dependency_restored", False),
                (
                    {"error": "independent business result"},
                    "same_feature_healthy",
                    False,
                ),
            ]
        for value, stage, failed in observations:
            current["value"] = value
            current["unavailable"] = failed
            arguments = {"payload": value, "payload_json": json.dumps(value)}
            for workflow in (
                "direct",
                "composite",
                "provider",
                "request_rule",
                "ai_task",
            ):
                expected = expected_consumer_result(kind, value, tmp_path, workflow)

                def prepare(value=value, failed=failed):
                    (tmp_path / "outcome.txt").write_text(
                        "START" if kind == "edit_file" else json.dumps(value),
                        encoding="utf-8",
                    )
                    with sqlite3.connect(tmp_path / "outcome.db") as db:
                        db.execute("CREATE TABLE IF NOT EXISTS probes (value TEXT)")
                        db.execute("DELETE FROM probes")
                        db.execute(
                            "INSERT INTO probes VALUES (?)", (json.dumps(value),)
                        )
                        if failed and kind == "sqlite":
                            db.execute("DROP TABLE probes")
                    if failed and kind == "read_file":
                        (tmp_path / "outcome.txt").unlink()

                await hass.async_add_executor_job(prepare)
                call_id = f"business-{workflow}-{len(captured)}"
                call = (
                    _responses_sse_tool_call
                    if api_mode == API_MODE_RESPONSES
                    else _chat_sse_tool_call
                )
                text = (
                    _responses_sse_text
                    if api_mode == API_MODE_RESPONSES
                    else _chat_sse_text
                )
                if workflow == "direct":
                    with propagate_function_execution_errors():
                        if failed:
                            with pytest.raises(HomeAssistantError):
                                await backend.execute(
                                    hass, validated, arguments, None, []
                                )
                            record(
                                stress_trace,
                                "shared_backend_failure",
                                backend=kind,
                                workflow=workflow,
                                api=api_mode,
                                stage=stage,
                            )
                            continue
                        actual = await backend.execute(
                            hass, validated, arguments, None, []
                        )
                elif workflow == "composite":
                    if failed:
                        with pytest.raises(HomeAssistantError):
                            await get_function("composite").execute(
                                hass, {"sequence": [validated]}, arguments, None, []
                            )
                        record(
                            stress_trace,
                            "shared_backend_failure",
                            backend=kind,
                            workflow=workflow,
                            api=api_mode,
                            stage=stage,
                        )
                        continue
                    actual = await get_function("composite").execute(
                        hass, {"sequence": [validated]}, arguments, None, []
                    )
                elif workflow == "request_rule":
                    for rule in agent._request_rules.snapshot()["rules"]:
                        await agent._request_rules.async_delete(rule["id"])
                    await agent._request_rules.async_create(
                        _local(
                            [
                                {
                                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                                    "data": {
                                        "function": "business_probe",
                                        "arguments": arguments,
                                        "result_alias": "business_value",
                                    },
                                },
                                {
                                    "action": "outcome_probe.capture",
                                    "data": {"value": "{{ business_value }}"},
                                },
                            ]
                        )
                    )
                    wire = _install_wire(monkeypatch, agent, [])
                    before_capture = len(captured)
                    response = await _say(hass, agent, "run rule")
                    if failed:
                        assert response.response.error_code is not None
                        assert (
                            response.response.as_dict()["speech"]["plain"]["speech"]
                            == "Failed safely"
                        )
                        assert not agent._usage.runs[-1].successful
                        assert len(captured) == before_capture
                        assert not wire.requests
                        record(
                            stress_trace,
                            "shared_backend_failure",
                            backend=kind,
                            workflow=workflow,
                            api=api_mode,
                            stage=stage,
                        )
                        continue
                    assert response.response.error_code is None, str(
                        response.response.as_dict()
                    )
                    assert _speech(response) == "Done"
                    assert not wire.requests
                    actual = captured[-1]
                else:
                    wire = (
                        _wire(
                            monkeypatch,
                            task_entry,
                            [
                                call(call_id, task_tool_name, arguments),
                                text("Completed"),
                            ],
                        )
                        if workflow == "ai_task"
                        else _install_wire(
                            monkeypatch,
                            agent,
                            [
                                call(call_id, "business_probe", arguments),
                                text("Completed"),
                            ],
                        )
                    )
                    if workflow == "ai_task":
                        if failed:
                            failed_task = await ai_task.async_generate_data(
                                hass,
                                task_name="Shared failure",
                                entity_id=task_entity,
                                instructions="Read unavailable dependency",
                                llm_api=caller,
                            )
                            assert failed_task.data == "Completed"
                            assert len(wire.requests) == 2
                            native_error = _provider_result(
                                wire.requests[1], api_mode, call_id
                            )
                            assert native_error.get("status") == "error", native_error
                            record(
                                stress_trace,
                                "shared_backend_failure",
                                backend=kind,
                                workflow=workflow,
                                api=api_mode,
                                stage=stage,
                            )
                            continue
                        assert (
                            await ai_task.async_generate_data(
                                hass,
                                task_name="Shared outcome",
                                entity_id=task_entity,
                                instructions="Read the business value",
                                llm_api=caller,
                            )
                        ).data == "Completed"
                    else:
                        if failed:
                            response = await _say(
                                hass, agent, "Read unavailable dependency"
                            )
                            assert response.response.error_code is not None
                            assert not agent._usage.runs[-1].successful
                            assert len(wire.requests) == 1
                            record(
                                stress_trace,
                                "shared_backend_failure",
                                backend=kind,
                                workflow=workflow,
                                api=api_mode,
                                stage=stage,
                            )
                            continue
                        assert (
                            _speech(await _say(hass, agent, "Read business value"))
                            == "Completed"
                        )
                    assert len(wire.requests) == 2
                    actual = _provider_result(wire.requests[1], api_mode, call_id)
                    if workflow == "ai_task":
                        actual = actual["result"]
                try:
                    assert_typed_value(actual, expected)
                except AssertionError as error:
                    raise AssertionError(
                        (kind, workflow, api_mode, value, actual, expected)
                    ) from error
                if kind in {"write_file", "edit_file"}:
                    assert await hass.async_add_executor_job(
                        (tmp_path / "outcome.txt").read_text, "utf-8"
                    ) == json.dumps(value)
                record(
                    stress_trace,
                    "summary",
                    campaign_action="shared_backend_outcome",
                    shared_backend_business_cases=int(stage == "business"),
                    backend=kind,
                    workflow=workflow,
                    api=api_mode,
                    value=value,
                    actual=actual,
                    stage=stage,
                )
        wire = _install_wire(
            monkeypatch, agent, [text("Independent healthy operation")]
        )
        assert (
            _speech(await _say(hass, agent, "Answer without tools"))
            == "Independent healthy operation"
        )
        assert len(wire.requests) == 1
    finally:
        for entry in entries:
            assert await hass.config_entries.async_unload(entry.entry_id)
        await runner.cleanup()


@pytest.mark.parametrize("boundary", ["release", "deadline", "cancel"])
async def test_sqlite_lock_deadline_and_worker_recovery_on_public_assist(
    hass, monkeypatch, tmp_path, stress_trace, boundary
):
    """Real native lock waits settle under the tool deadline, even after cancellation."""
    path = tmp_path / "locked.db"
    connect = sqlite3.connect
    writer = connect(path)
    writer.execute("CREATE TABLE probes (value TEXT)")
    writer.execute("INSERT INTO probes VALUES ('HEALTHY-SQLITE')")
    writer.commit()
    entry = _make_entry(
        "SQLite lock recovery",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": "locked_read",
                        "description": "Read local probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "sqlite",
                        "db_url": str(path),
                        "query": "SELECT value FROM probes",
                        "timeout": 0.15,
                    },
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    entered, closed = threading.Event(), threading.Event()

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "SELECT value FROM probes":
                entered.set()
            return super().execute(sql, *args, **kwargs)

        def close(self):
            super().close()
            closed.set()

    def observed_connect(url, *args, **kwargs):
        if "locked.db" in str(url) and kwargs.get("uri"):
            kwargs["factory"] = ObservedConnection
        return connect(url, *args, **kwargs)

    monkeypatch.setattr(sqlite_module.sqlite3, "connect", observed_connect)
    wire = _install_wire(
        monkeypatch,
        agent,
        _provider_replies(
            API_MODE_CHAT_COMPLETIONS, "locked-call", "locked_read", "Read settled"
        ),
    )
    writer.execute("BEGIN EXCLUSIVE")
    probe = connect(path, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            probe.execute("SELECT value FROM probes")
    finally:
        probe.close()
    started = time.monotonic()
    pending = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Read local probe",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.sleep(0.03)
        assert not closed.is_set(), "Worker must actually be blocked by the held lock"
        if boundary == "release":
            writer.rollback()
            result = await asyncio.wait_for(pending, 5)
            assert _speech(result) == "Read settled"
            assert _provider_result(
                wire.requests[1], API_MODE_CHAT_COMPLETIONS, "locked-call"
            ) == [{"value": "HEALTHY-SQLITE"}]
        elif boundary == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            result = await asyncio.wait_for(pending, 5)
            assert _speech(result) == "Read settled"
            assert "execution deadline" in str(
                _provider_result(
                    wire.requests[1], API_MODE_CHAT_COMPLETIONS, "locked-call"
                )
            )
        # The native worker must close while the exclusive lock is STILL held in
        # deadline/cancellation variants. Cancelling the coroutine is insufficient.
        assert await asyncio.to_thread(closed.wait, 1)
        elapsed = time.monotonic() - started
        assert elapsed < 2
    finally:
        writer.rollback()
        writer.close()
        await asyncio.gather(pending, return_exceptions=True)
    healthy_wire = _install_wire(
        monkeypatch,
        agent,
        _provider_replies(
            API_MODE_CHAT_COMPLETIONS, "healthy-call", "locked_read", "Recovered"
        ),
    )
    healthy = await conversation.async_converse(
        hass=hass,
        text="Read again",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(healthy) == "Recovered"
    assert _provider_result(
        healthy_wire.requests[1], API_MODE_CHAT_COMPLETIONS, "healthy-call"
    ) == [{"value": "HEALTHY-SQLITE"}]
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        public_turns=2,
        actual_function_executions=2,
        sqlite_lock_waits=1,
        sqlite_worker_settlements=1,
        sqlite_healthy_recoveries=1,
    )


def _multicall_reply(mode, calls):
    """Use the existing real SDK stream builders for an ordered batch."""
    from tests.test_openai_sdk_wire import _chat_chunk, _sse
    from tests_real_ha.test_function_execution_composition import (
        _responses_sse_tool_calls,
    )

    if mode == API_MODE_RESPONSES:
        return _responses_sse_tool_calls(calls)
    return _sse(
        [
            _chat_chunk(
                delta={
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                        for index, (call_id, name, arguments) in enumerate(calls)
                    ],
                },
                finish_reason="tool_calls",
            )
        ]
    )


def _provider_replies(mode: str, call_id: str, name: str, text: str) -> list[bytes]:
    if mode == API_MODE_RESPONSES:
        return [_responses_sse_tool_call(call_id, name, {}), _responses_sse_text(text)]
    return [_chat_sse_tool_call(call_id, name, {}), _chat_sse_text(text)]


def _provider_result(request: dict[str, Any], mode: str, call_id: str) -> Any:
    body = request["body"]
    if mode == API_MODE_RESPONSES:
        call = next(
            item for item in body["input"] if item.get("type") == "function_call"
        )
        assert call["call_id"] == call_id
        item = next(
            item for item in body["input"] if item.get("type") == "function_call_output"
        )
        assert item["call_id"] == call_id
        serialized = item["output"]
    else:
        assistant = next(
            message for message in body["messages"] if message.get("tool_calls")
        )
        assert assistant["tool_calls"][0]["id"] == call_id
        item = next(
            message for message in body["messages"] if message.get("role") == "tool"
        )
        assert item["tool_call_id"] == call_id
        serialized = item["content"]
    return json.loads(serialized)["result"]


def _assert_exchange(wire: Any, mode: str, name: str) -> None:
    path = "/v1/responses" if mode == API_MODE_RESPONSES else "/v1/chat/completions"
    assert [request["path"] for request in wire.requests] == [path, path]
    assert name in _tool_names(wire.requests[0]["body"], mode)


def _configuration(kind: str, root: Path, url: str) -> dict[str, Any]:
    marker = root / "marker.txt"
    if kind == "rest":
        return {"type": kind, "resource": f"{url}/json", "method": "GET"}
    if kind == "scrape":
        return {
            "type": kind,
            "resource": f"{url}/html",
            "sensor": [{"select": ".probe", "name": "probe"}],
        }
    if kind == "composite":
        return {
            "type": kind,
            "sequence": [
                {
                    "type": "template",
                    "value_template": "COMPOSITE-FIRST",
                    "response_variable": "first",
                },
                {"type": "template", "value_template": "{{ first }}-SECOND"},
            ],
        }
    if kind == "sqlite":
        return {
            "type": kind,
            "db_url": f"file:{root / 'wire.db'}",
            "query": "SELECT value FROM probes WHERE id = 1",
            "single": True,
        }
    if kind == "bash":
        return {
            "type": kind,
            "command": "printf EOAI_BASH_WIRE",
            "allow_unsafe_shell": True,
            "cwd": str(root),
        }
    if kind == "read_file":
        return {"type": kind, "path": str(marker), "allow_dir": [str(root)]}
    if kind == "write_file":
        return {
            "type": kind,
            "path": str(marker),
            "content": "EOAI_WRITE_WIRE",
            "allow_dir": [str(root)],
        }
    if kind == "edit_file":
        return {
            "type": kind,
            "path": str(marker),
            "old_text": "BEFORE_EDIT",
            "new_text": "EOAI_EDIT_WIRE",
            "allow_dir": [str(root)],
        }
    raise AssertionError(kind)


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("kind", REMAINING_TYPES)
async def test_remaining_function_type_executes_on_provider_wire(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    socket_enabled: Any,
    tmp_path: Path,
    stress_trace: list[dict],
    kind: str,
    api_mode: str,
) -> None:
    del socket_enabled  # Local HTTP fixture and HA's real REST client use loopback.
    calls: list[str] = []

    async def json_response(_request: web.Request) -> web.Response:
        calls.append("rest")
        return web.json_response({"marker": "EOAI_REST_WIRE"})

    async def html_response(_request: web.Request) -> web.Response:
        calls.append("scrape")
        return web.Response(
            text='<html><span class="probe">EOAI_SCRAPE_WIRE</span></html>',
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/json", json_response)
    app.router.add_get("/html", html_response)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        assert site._server is not None
        port = site._server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        marker = tmp_path / "marker.txt"
        marker.write_text(
            "BEFORE_EDIT" if kind == "edit_file" else "EOAI_READ_WIRE",
            encoding="utf-8",
        )
        with sqlite3.connect(tmp_path / "wire.db") as db:
            db.execute("CREATE TABLE probes (id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO probes VALUES (1, 'EOAI_SQLITE_WIRE')")
        tool_name = f"enhanced_{kind}_wire"
        entry = _make_entry(
            f"Enhanced {kind} Function wire",
            include_ai_task=False,
            conversation_options={
                CONF_API_MODE: api_mode,
                CONF_FUNCTION_TOOLS: [
                    {
                        "spec": {
                            "name": tool_name,
                            "description": f"Execute local {kind} fixture",
                            "parameters": {"type": "object", "properties": {}},
                        },
                        "function": _configuration(kind, tmp_path, url),
                        "enabled": True,
                    }
                ],
            },
        )
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        call_id = f"call-{kind}"
        wire = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(api_mode, call_id, tool_name, "Fixture complete"),
        )
        response = await conversation.async_converse(
            hass=hass,
            text=f"Execute {kind}",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(response) == "Fixture complete"
        _assert_exchange(wire, api_mode, tool_name)
        result = _provider_result(wire.requests[1], api_mode, call_id)
        if isinstance(result, dict):
            assert "error" not in result, result
        if kind == "rest":
            assert json.loads(result) == {"marker": "EOAI_REST_WIRE"}
            assert calls == ["rest"]
        elif kind == "scrape":
            assert result == "EOAI_SCRAPE_WIRE"
            assert calls == ["scrape"]
        elif kind == "composite":
            assert result == "COMPOSITE-FIRST-SECOND"
        elif kind == "sqlite":
            assert result == {"value": "EOAI_SQLITE_WIRE"}
        elif kind == "bash":
            assert result == {"exit_code": 0, "stdout": "EOAI_BASH_WIRE"}
        elif kind == "read_file":
            assert result == {"content": "EOAI_READ_WIRE", "size": 14}
        elif kind == "write_file":
            assert marker.read_text(encoding="utf-8") == "EOAI_WRITE_WIRE"
            assert result == {"success": True, "path": str(marker), "bytes_written": 15}
        elif kind == "edit_file":
            assert marker.read_text(encoding="utf-8") == "EOAI_EDIT_WIRE"
            assert result == {"success": True, "path": str(marker), "replacements": 1}
        record(
            stress_trace,
            "summary",
            layer="provider-wire",
            public_turns=1,
            provider_requests=2,
            actual_function_executions=1,
            **{f"{kind}_function_executions": 1},
        )
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("failure", ERROR_CASES)
async def test_remaining_function_errors_are_serialized_on_provider_wire(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    socket_enabled: Any,
    tmp_path: Path,
    stress_trace: list[dict],
    failure: str,
    api_mode: str,
) -> None:
    del socket_enabled
    hits: list[str] = []

    async def html_response(_request: web.Request) -> web.Response:
        hits.append("html")
        return web.Response(
            text="<html><span>Other content</span></html>", content_type="text/html"
        )

    async def missing_response(_request: web.Request) -> web.Response:
        hits.append("rest")
        return web.Response(status=404, text="404: Not Found")

    app = web.Application()
    app.router.add_get("/html", html_response)
    app.router.add_get("/missing", missing_response)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        assert site._server is not None
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        kind = failure.split("_")[0]
        if failure.startswith(("read_file", "write_file", "edit_file")):
            kind = (
                failure.rsplit("_", 1)[0]
                if failure != "edit_file_no_match"
                else "edit_file"
            )
        config = _configuration(kind, tmp_path, url)
        if failure == "rest_404":
            config["resource"] = f"{url}/missing"
        elif failure == "scrape_missing_selector":
            config["sensor"] = [{"select": ".absent", "name": "absent"}]
        elif failure == "sqlite_bad_query":
            config["query"] = "SELECT value FROM absent_table"
        elif failure == "bash_nonzero":
            config["command"] = (
                f"printf 'attempt\\n' >> '{tmp_path / 'attempts.txt'}'; printf EOAI_FAILURE >&2; exit 7"
            )
        elif failure == "read_file_missing":
            config["path"] = str(tmp_path / "absent.txt")
        elif failure == "write_file_denied":
            (tmp_path / "marker.txt").write_text("PRESERVE", encoding="utf-8")
            config["allow_dir"] = [str(tmp_path / "allowed")]
        elif failure == "edit_file_no_match":
            (tmp_path / "marker.txt").write_text("PRESERVE", encoding="utf-8")
            config["old_text"] = "ABSENT_TEXT"
        if kind == "sqlite":
            with sqlite3.connect(tmp_path / "wire.db") as db:
                db.execute("CREATE TABLE probes (id INTEGER PRIMARY KEY, value TEXT)")
        name = f"enhanced_{failure}_wire"
        entry = _make_entry(
            f"Enhanced {failure} wire error",
            include_ai_task=False,
            conversation_options={
                CONF_API_MODE: api_mode,
                CONF_FUNCTION_TOOLS: [
                    {
                        "spec": {
                            "name": name,
                            "description": f"Exercise {failure} failure",
                            "parameters": {"type": "object", "properties": {}},
                        },
                        "function": config,
                        "enabled": True,
                    }
                ],
            },
        )
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        call_id = f"call-{failure}"
        wire = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(api_mode, call_id, name, "Failure handled"),
        )
        response = await conversation.async_converse(
            hass=hass,
            text=f"Execute {failure}",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(response) == "Failure handled"
        _assert_exchange(wire, api_mode, name)
        result = _provider_result(wire.requests[1], api_mode, call_id)
        if failure == "bash_nonzero":
            assert result == {"exit_code": 7, "stderr": "EOAI_FAILURE", "stdout": ""}
            assert (tmp_path / "attempts.txt").read_text(
                encoding="utf-8"
            ).splitlines() == ["attempt"]
        elif failure == "scrape_missing_selector":
            assert hits == ["html"]
            assert result is None
        elif failure == "rest_404":
            assert hits == ["rest"]
            assert result == {"status": "error", "error": "REST request failed: HTTP 404 Not Found"}
        elif failure == "sqlite_bad_query":
            assert result == {
                "status": "error",
                "error": "SQLite query failed: no such table: absent_table",
            }
        elif failure == "read_file_missing":
            assert result == {"error": f"File not found: {tmp_path / 'absent.txt'}"}
        elif failure == "write_file_denied":
            assert result == {
                "error": f"Access denied: path '{tmp_path / 'marker.txt'}' is not in allowed directories"
            }
            assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "PRESERVE"
        else:
            assert result == {"error": "Text not found in file: ABSENT_TEXT..."}
            assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "PRESERVE"
        record(
            stress_trace,
            "summary",
            layer="provider-wire",
            public_turns=1,
            provider_requests=2,
            provider_wire_function_errors=1,
            failure=failure,
        )
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("api_mode", API_MODES)
async def test_composite_late_failure_preserves_one_completed_side_effect(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stress_trace: list[dict],
    api_mode: str,
) -> None:
    side_effects = tmp_path / "side-effects.txt"
    missing = tmp_path / "missing.txt"
    name = "composite_partial_failure_wire"
    entry = _make_entry(
        "Composite partial failure",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": name,
                        "description": "Run two steps",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "composite",
                        "sequence": [
                            {
                                "type": "bash",
                                "command": f"printf 'first\\n' >> '{side_effects}'",
                                "allow_unsafe_shell": True,
                                "cwd": str(tmp_path),
                            },
                            {
                                "type": "read_file",
                                "path": str(missing),
                                "allow_dir": [str(tmp_path)],
                            },
                        ],
                    },
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    call_id = "call-composite-partial"
    wire = _install_wire(
        monkeypatch,
        agent,
        _provider_replies(api_mode, call_id, name, "Partial failure handled"),
    )
    response = await conversation.async_converse(
        hass=hass,
        text="Run composite",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(response) == "Partial failure handled"
    _assert_exchange(wire, api_mode, name)
    result = _provider_result(wire.requests[1], api_mode, call_id)
    assert result == {"status": "error", "error": f"File not found: {missing}"}
    assert side_effects.read_text(encoding="utf-8").splitlines() == ["first"]
    record(stress_trace, "composite_partial_failure", mode=api_mode, side_effects=1)


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize(
    "variant", ["safe-overlap", "mixed-serial", "sibling-failure", "parent-cancel"]
)
async def test_public_assist_multicall_dispatch_concurrency(
    hass, monkeypatch, stress_trace, api_mode, variant
):
    """SDK parsing and public Assist retain safe overlap and serial side effects."""
    import asyncio

    from pytest_homeassistant_custom_component.common import MockUser

    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )

    from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
        is_tool_result_content,
        tool_result_data,
    )

    captured = []
    add = conversation.ChatLog.async_add_assistant_content_without_tools

    def capture(log, content):
        if is_tool_result_content(content):
            captured.append(content)
        add(log, content)

    monkeypatch.setattr(
        conversation.ChatLog, "async_add_assistant_content_without_tools", capture
    )
    mixed = variant == "mixed-serial"
    from homeassistant.exceptions import HomeAssistantError

    owner = MockUser(
        id="parallel-owner", name="Parallel owner", is_owner=True
    ).add_to_hass(hass)
    started = {name: asyncio.Event() for name in ("a", "b")}
    release = {name: asyncio.Event() for name in ("a", "b")}
    completed = {name: asyncio.Event() for name in ("a", "b")}
    effects = []
    cancelled = set()
    native = get_function("native")
    original = native.get_user_from_user_id

    async def gated(*args):
        marker = args[2].get("marker", "a")
        started[marker].set()
        try:
            await release[marker].wait()
        except asyncio.CancelledError:
            cancelled.add(marker)
            raise
        if variant == "sibling-failure" and marker == "b":
            completed[marker].set()
            raise HomeAssistantError("EXPECTED-SIBLING-FAILURE")
        result = await original(*args)
        completed[marker].set()
        return result

    monkeypatch.setattr(native, "get_user_from_user_id", gated)

    async def effect(call):
        started["b"].set()
        effects.append(call.data["marker"])
        completed["b"].set()

    hass.services.async_register("concurrency_probe", "record", effect)
    tools = [
        {
            "spec": {
                "name": f"probe_{marker}",
                "description": "Read caller identity",
                "parameters": {
                    "type": "object",
                    "properties": {"marker": {"type": "string"}},
                    "required": ["marker"],
                },
            },
            "function": {"type": "native", "name": "get_user_from_user_id"},
        }
        for marker in ("a", "b")
    ]
    if mixed:
        tools[1]["function"] = {
            "type": "script",
            "sequence": [
                {
                    "action": "concurrency_probe.record",
                    "data": {"marker": "{{ marker }}"},
                }
            ],
        }
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    )

    entry = _make_entry(
        "Assist concurrency",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_FUNCTION_TOOLS: tools,
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: True,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    calls = [
        (f"call-{marker}", f"probe_{marker}", {"marker": marker})
        for marker in ("a", "b")
    ]
    first = _multicall_reply(api_mode, calls)
    final = _responses_sse_text if api_mode == API_MODE_RESPONSES else _chat_sse_text
    wire = _install_wire(monkeypatch, agent, [first, final("Both complete")])
    turn = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Run both probes",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    try:
        await asyncio.wait_for(started["a"].wait(), 10)
        if mixed:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(started["b"].wait(), 0.1)
            assert effects == []
        else:
            await asyncio.wait_for(started["b"].wait(), 10)
            assert not completed["a"].is_set()
            if variant == "parent-cancel":
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
                assert cancelled == {"a", "b"}
                assert len(wire.requests) == 1
            else:
                release["b"].set()
                await asyncio.wait_for(completed["b"].wait(), 10)
                assert not completed["a"].is_set()
                assert not turn.done()
                assert cancelled == set()
        release["a"].set()
        if variant == "parent-cancel":
            release["b"].set()
            healthy_wire = _install_wire(
                monkeypatch,
                agent,
                [
                    _multicall_reply(
                        api_mode, [("healthy", "probe_a", {"marker": "a"})]
                    ),
                    final("Recovered"),
                ],
            )
            # Recovery uses a fresh request after the cancelled history is settled.
            healthy = await conversation.async_converse(
                hass=hass,
                text="Read again",
                conversation_id=None,
                context=Context(user_id=owner.id),
                language="en",
                agent_id=entry.entry_id,
            )
            assert _speech(healthy) == "Recovered"
            assert len(healthy_wire.requests) == 2
            record(
                stress_trace,
                "summary",
                layer="provider-wire",
                assist_parent_cancel_cases=1,
                recovery_conversations=1,
            )
            return
        result = await asyncio.wait_for(turn, 10)
        if variant == "sibling-failure":
            assert result.response.error_code is not None
            assert [content.tool_call_id for content in captured] == [
                "call-a",
                "call-b",
            ]
            assert "Parallel owner" in json.dumps(tool_result_data(captured[0]))
            assert "EXPECTED-SIBLING-FAILURE" in json.dumps(
                tool_result_data(captured[1])
            )
            assert cancelled == set()
            assert len(wire.requests) == 1
            healthy_wire = _install_wire(
                monkeypatch,
                agent,
                [
                    _multicall_reply(
                        api_mode, [("healthy", "probe_a", {"marker": "a"})]
                    ),
                    final("Recovered"),
                ],
            )
            healthy = await conversation.async_converse(
                hass=hass,
                text="Read again",
                conversation_id=None,
                context=Context(user_id=owner.id),
                language="en",
                agent_id=entry.entry_id,
            )
            assert _speech(healthy) == "Recovered"
            assert len(healthy_wire.requests) == 2
            record(
                stress_trace,
                "summary",
                layer="provider-wire",
                assist_sibling_failure_cases=1,
                recovery_conversations=1,
            )
            return
        assert _speech(result) == "Both complete"
        assert all(event.is_set() for event in completed.values())
        assert effects == (["b"] if mixed else [])
        body = wire.requests[1]["body"]
        outputs = (
            [
                item
                for item in body["input"]
                if item.get("type") == "function_call_output"
            ]
            if api_mode == API_MODE_RESPONSES
            else [item for item in body["messages"] if item.get("role") == "tool"]
        )
        assert [
            item["call_id" if api_mode == API_MODE_RESPONSES else "tool_call_id"]
            for item in outputs
        ] == ["call-a", "call-b"]
        assert "Parallel owner" in json.dumps(outputs[0])
        assert len(wire.requests) == 2
        record(
            stress_trace,
            "summary",
            campaign_action="public_assist_concurrency",
            layer="provider-wire",
            api_mode=api_mode,
            mixed=mixed,
            function_executions=2,
            conversations=1,
            safe_overlap=int(not mixed),
            assist_safe_overlap=int(not mixed),
            assist_mixed_serial=int(mixed),
            assist_sibling_failure_cases=int(variant == "sibling-failure"),
        )
    finally:
        for event in release.values():
            event.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("kind", ["rest", "scrape"])
@pytest.mark.parametrize(
    "fault",
    [
        "length",
        "chunked",
        "trickle",
        "disconnect",
        "encoding",
        "gzip-valid",
        "deflate-valid",
        "gzip-expansion",
        "deflate-expansion",
        "gzip-corrupt",
        "deflate-corrupt",
        "gzip-truncated",
        "deflate-truncated",
    ],
)
async def test_remote_function_socket_fault_recovers(
    hass, monkeypatch, socket_enabled, tmp_path, stress_trace, api_mode, kind, fault
):
    """Real HA aiohttp/RestData bounds malformed remote Function Tool responses."""
    import asyncio
    from contextlib import suppress

    from custom_components.extended_openai_conversation_responses.resource_limits import (
        MAX_REMOTE_RESPONSE_BYTES,
    )

    del socket_enabled
    attempts = []
    release = asyncio.Event()
    healthy = False

    async def remote(request):
        attempts.append("healthy" if healthy else fault)
        if healthy:
            return web.Response(
                text='<html><span class="probe">RECOVERED-REMOTE</span></html>'
                if kind == "scrape"
                else "RECOVERED-REMOTE",
                content_type="text/html" if kind == "scrape" else "text/plain",
            )
        if fault.startswith(("gzip-", "deflate-")):
            import gzip
            import zlib

            codec, shape = fault.split("-")
            body = b'<html><span class="probe">ENCODED-REMOTE</span>'
            if shape == "expansion":
                body += b"x" * (MAX_REMOTE_RESPONSE_BYTES + 1)
            body += b"</html>"
            encoded = gzip.compress(body) if codec == "gzip" else zlib.compress(body)
            assert len(encoded) < MAX_REMOTE_RESPONSE_BYTES
            if shape == "corrupt":
                encoded = encoded[:8] + b"corrupt-compressed-data" + encoded[8:]
            elif shape == "truncated":
                encoded = encoded[:-8] if codec == "gzip" else encoded[:-4]
            return web.Response(
                body=encoded,
                headers={
                    "Content-Encoding": codec,
                    "Content-Type": "text/html; charset=utf-8",
                },
            )
        response = web.StreamResponse(
            headers={"Content-Type": "text/html; charset=utf-8"}
        )
        if fault == "length":
            response.headers["Content-Length"] = str(MAX_REMOTE_RESPONSE_BYTES + 1)
        elif fault == "disconnect":
            response.headers["Content-Length"] = "4096"
        await response.prepare(request)
        try:
            if fault == "length":
                await release.wait()
            elif fault == "chunked":
                # If the body limit regresses, Scrape must return a real match
                # instead of failing coincidentally because its selector is absent.
                await response.write(b'<span class="probe">OVERSIZED-REMOTE</span>')
                for _ in range(MAX_REMOTE_RESPONSE_BYTES // 65536 + 2):
                    await response.write(b"<p>" + b"x" * 65530 + b"</p>")
            elif fault == "trickle":
                await response.write(b"<html>")
                await release.wait()
            elif fault == "disconnect":
                await response.write(b"incomplete-body")
                request.transport.close()
            else:
                await response.write(b"\xff\xfeinvalid-utf8")
            await response.write_eof()
        except ConnectionError, RuntimeError:
            pass  # The expected client rejection closes an oversized response.
        return response

    app = web.Application()
    app.router.add_get("/{path}", remote)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        config = _configuration(kind, tmp_path, url)
        config["timeout"] = 1
        name = "remote_boundary"
        tool = {
            "spec": {
                "name": name,
                "description": "Read local remote fixture",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": config,
        }
        entry = _make_entry(
            "Remote boundary",
            include_ai_task=False,
            conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]},
        )
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        wire = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(api_mode, "call-fault", name, "Remote fault handled"),
        )

        async def say():
            return await conversation.async_converse(
                hass=hass,
                text="Read remote probe",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
            )

        failed = await asyncio.wait_for(say(), 10)
        if fault.endswith("-valid"):
            assert failed.response.error_code is None
            assert _provider_result(wire.requests[1], api_mode, "call-fault") == (
                "ENCODED-REMOTE"
                if kind == "scrape"
                else '<html><span class="probe">ENCODED-REMOTE</span></html>'
            )
        elif failed.response.error_code is None:
            assert len(wire.requests) == 2
            value = _provider_result(wire.requests[1], api_mode, "call-fault")
            assert isinstance(value, dict) and value.get("status") == "error", value
        assert attempts == [fault]
        healthy = True
        release.set()
        recovered_wire = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(api_mode, "call-recovery", name, "Recovered remote"),
        )
        recovered = await asyncio.wait_for(say(), 10)
        assert _speech(recovered) == "Recovered remote"
        assert (
            _provider_result(recovered_wire.requests[1], api_mode, "call-recovery")
            == "RECOVERED-REMOTE"
        )
        assert attempts == [fault, "healthy"]
        record(
            stress_trace,
            "summary",
            campaign_action="remote_resource_boundary",
            layer="provider-wire",
            api_mode=api_mode,
            kind=kind,
            fault=fault,
            remote_resource_recovery_cases=1,
            remote_failures=int(not fault.endswith("-valid")),
            compressed_remote_cases=int(fault.startswith(("gzip-", "deflate-"))),
            recovery_conversations=1,
            response_limit_bytes=MAX_REMOTE_RESPONSE_BYTES,
        )
    finally:
        release.set()
        with suppress(ConnectionError):
            await runner.cleanup()


@pytest.mark.parametrize("shape", ["deep", "deep-singleton", "wide", "cyclic"])
async def test_composite_resource_tree_rejected_before_effects(
    hass, monkeypatch, stress_trace, shape
):
    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )
    from homeassistant.exceptions import HomeAssistantError

    leaf = {"type": "native", "name": "get_user_from_user_id"}
    config = leaf
    if shape in {"deep", "deep-singleton"}:
        for _ in range(512):
            config = {
                "type": "composite",
                "sequence": [config] if shape == "deep" else config,
            }
    elif shape == "wide":
        config = {"type": "composite", "sequence": [leaf] * 257}
    else:
        config = {"type": "composite", "sequence": []}
        config["sequence"].append(config)
    composite = get_function("composite")
    dispatched = []

    async def unexpected(*args):
        dispatched.append(args)
        raise AssertionError("Rejected tree dispatched a child")

    monkeypatch.setattr(get_function("native"), "execute", unexpected)
    with pytest.raises(
        HomeAssistantError, match=r"Composite function.*(safety limits|recursive)"
    ):
        composite.validate_schema(config)
    with pytest.raises(
        HomeAssistantError, match=r"Composite function.*(safety limits|recursive)"
    ):
        await composite.execute(hass, config, {}, None, [])
    assert dispatched == []
    record(
        stress_trace,
        "summary",
        campaign_action="composite_resource_rejection",
        layer="model-level",
        shape=shape,
        composite_rejected_trees=1,
        rejected_trees=1,
        dispatched_children=0,
    )


async def test_nested_composite_cancellation_stops_later_side_effect_and_recovers(
    hass, monkeypatch, stress_trace
):
    import asyncio

    started, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def action(call):
        effects.append(call.data["marker"])
        if call.data["marker"] == "first":
            started.set()
            await release.wait()

    hass.services.async_register("composite_probe", "record", action)
    config = {
        "type": "composite",
        "sequence": [
            {
                "type": "script",
                "sequence": [
                    {"action": "composite_probe.record", "data": {"marker": marker}}
                ],
            }
            for marker in ("first", "later")
        ],
    }
    for _ in range(8):
        config = {"type": "composite", "sequence": [config]}
    name = "nested_probe"
    entry = _make_entry(
        "Nested cancellation",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": name,
                        "description": "Nested cancellation probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": config,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(
        monkeypatch,
        agent,
        _provider_replies(
            API_MODE_RESPONSES, "call-cancel", name, "Should not complete"
        ),
    )

    async def say():
        return await conversation.async_converse(
            hass=hass,
            text="Run nested probe",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    turn = asyncio.create_task(say())
    try:
        await asyncio.wait_for(started.wait(), 10)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert effects == ["first"]
        assert len(wire.requests) == 1
        release.set()
        fresh = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(
                API_MODE_RESPONSES, "call-fresh", name, "Nested recovered"
            ),
        )
        result = await asyncio.wait_for(say(), 10)
        assert _speech(result) == "Nested recovered"
        assert effects == ["first", "first", "later"]
        assert len(fresh.requests) == 2
        record(
            stress_trace,
            "summary",
            campaign_action="composite_cancellation",
            layer="provider-wire",
            composite_cancelled_exchanges=1,
            cancelled_exchanges=1,
            recovery_conversations=1,
            effects_after_cancel=1,
        )
    finally:
        release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.parametrize("boundary", ["timeout", "cancel"])
async def test_bash_exited_leader_pipe_descendants_settle_on_public_assist(
    hass, monkeypatch, tmp_path, stress_trace, boundary
):
    """A real exited shell's inheriting child cannot hold owned readers indefinitely."""
    import os
    import shlex
    import signal
    import sys

    import psutil

    from custom_components.extended_openai_conversation_responses.functions import (
        bash as bash_module,
    )

    child_file = tmp_path / "child.pid"
    child_code = "import time; print('CHILD-STDOUT',flush=True); print('CHILD-STDERR',file=__import__('sys').stderr,flush=True); time.sleep(60)"
    leader_code = f"import subprocess, pathlib; child=subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); pathlib.Path({str(child_file)!r}).write_text(str(child.pid))"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(leader_code)}"
    processes, readers = [], []
    pipe_started = asyncio.Event()
    create = asyncio.create_subprocess_shell
    read = bash_module._read_bounded_stream

    async def observed_create(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        if len(processes) == 1:
            # Observe the real scheduling boundary: the leader has already exited
            # before execute reaches Process.wait(), while both pipes remain owned.
            async with asyncio.timeout(10):
                while process.returncode is None or not child_file.exists():
                    await asyncio.sleep(0.01)
            assert process.returncode == 0
            child = psutil.Process(int(child_file.read_text()))
            assert child.is_running()
            assert os.getpgid(child.pid) == process.pid
        return process

    async def observed_read(stream, *args):
        readers.append(asyncio.current_task())
        if len(readers) == 2:
            pipe_started.set()
        return await read(stream, *args)

    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", observed_create)
    monkeypatch.setattr(bash_module, "_read_bounded_stream", observed_read)
    tool = {
        "spec": {
            "name": "tree_probe",
            "description": "Owned process probe",
            "parameters": {
                "type": "object",
                "properties": {
                    "healthy": {"type": "boolean"},
                    "timeout": {"type": "number"},
                },
            },
        },
        "function": {
            "type": "bash",
            "command": "{% if healthy %}printf HEALTHY-TREE{% else %}"
            + command
            + "{% endif %}",
            "cwd": str(tmp_path),
            "restrict_to_workspace": False,
            "allow_unsafe_shell": True,
        },
    }
    entry = _make_entry(
        "Owned Bash descendants",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _multicall_reply(
                API_MODE_CHAT_COMPLETIONS,
                [
                    (
                        "tree",
                        "tree_probe",
                        {
                            "healthy": False,
                            "timeout": 0.3 if boundary == "timeout" else 10,
                        },
                    )
                ],
            ),
            _chat_sse_text("Tree settled"),
        ],
    )

    async def say():
        return await conversation.async_converse(
            hass=hass,
            text="Execute tree probe",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    turn = asyncio.create_task(say())
    try:
        await asyncio.wait_for(pipe_started.wait(), 10)
        assert processes[0].returncode == 0
        assert all(not reader.done() for reader in readers)
        child_pid = int(child_file.read_text())
        assert psutil.Process(child_pid).is_running()
        if boundary == "cancel":
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
        else:
            result = await asyncio.wait_for(turn, 5)
            assert _speech(result) == "Tree settled"
            assert (
                "timed out"
                in _provider_result(
                    wire.requests[1], API_MODE_CHAT_COMPLETIONS, "tree"
                )["error"]
            )
        assert all(reader.done() for reader in readers)
        async with asyncio.timeout(5):
            while (
                psutil.pid_exists(child_pid)
                and psutil.Process(child_pid).status() != psutil.STATUS_ZOMBIE
            ):
                await asyncio.sleep(0.02)
        healthy_wire = _install_wire(
            monkeypatch,
            agent,
            [
                _multicall_reply(
                    API_MODE_CHAT_COMPLETIONS,
                    [("healthy-tree", "tree_probe", {"healthy": True, "timeout": 2})],
                ),
                _chat_sse_text("Recovered tree"),
            ],
        )
        assert _speech(await asyncio.wait_for(say(), 10)) == "Recovered tree"
        assert _provider_result(
            healthy_wire.requests[1], API_MODE_CHAT_COMPLETIONS, "healthy-tree"
        ) == {"exit_code": 0, "stdout": "HEALTHY-TREE"}
        record(
            stress_trace,
            "summary",
            layer="provider-wire",
            bash_pipe_tree_cases=1,
            bash_pipe_settlements=1,
            recovery_conversations=1,
        )
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.gather(*readers, return_exceptions=True)


async def test_shared_ha_http_pool_waiters_recover_after_tool_cancellation(
    hass, monkeypatch, socket_enabled, tmp_path, stress_trace
):
    """Real REST/Scrape calls saturate one finite HA connector, then free its waiters."""
    import httpx
    from custom_components.extended_openai_conversation_responses.functions import (
        web as web_module,
    )
    from tests_real_ha.test_provider_wire_e2e import _raw_client

    del socket_enabled
    release = asyncio.Event()
    started = []
    sessions = []
    install = web_module._install_bounded_session
    original_limits = {}

    def observe_session(hass, data):
        install(hass, data)
        session = data._session._session
        sessions.append(session)
        connector = session.connector
        if connector not in original_limits:
            original_limits[connector] = connector._limit
            connector._limit = 2

    monkeypatch.setattr(web_module, "_install_bounded_session", observe_session)

    async def slow(request):
        started.append(request.path)
        await release.wait()
        return web.Response(text='<span class="probe">POOL-HEALTHY</span>')

    async def healthy(request):
        return web.Response(text="UNRELATED-HEALTHY")

    app = web.Application()
    app.router.add_get("/slow/{marker}", slow)
    app.router.add_get("/healthy", healthy)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    tools = []
    for kind in ("rest", "scrape"):
        config = _configuration(kind, tmp_path, url)
        config.pop("resource")
        config["resource_template"] = url + "/slow/{{ marker }}"
        config["timeout"] = 15
        tools.append(
            {
                "spec": {
                    "name": f"pool_{kind}",
                    "description": "Read pool probe",
                    "parameters": {
                        "type": "object",
                        "properties": {"marker": {"type": "string"}},
                        "required": ["marker"],
                    },
                },
                "function": config,
            }
        )
    entry = _make_entry(
        "HA pool pressure",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: tools,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)

    async def send(request, *args, **kwargs):
        body = json.loads(request.content)
        if any(item.get("role") == "tool" for item in body["messages"]):
            content = _chat_sse_text("Pool tool recovered")
        else:
            marker = body["messages"][-1]["content"]
            name = "pool_scrape" if marker == "b" else "pool_rest"
            content = _multicall_reply(
                API_MODE_CHAT_COMPLETIONS,
                [(f"pool-{marker}", name, {"marker": marker})],
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)

    async def say(marker):
        return await conversation.async_converse(
            hass=hass,
            text=marker,
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    turns = [asyncio.create_task(say(marker)) for marker in ("a", "b")]
    unrelated = None
    try:
        async with asyncio.timeout(10):
            while len(started) < 2:
                await asyncio.sleep(0.01)
        assert set(started) == {"/slow/a", "/slow/b"}
        assert len({id(session) for session in sessions}) == 1
        session = sessions[0]
        connector = session.connector
        assert len(connector._acquired) == 2

        async def ordinary_http():
            async with session.get(url + "/healthy") as response:
                return await response.text()

        unrelated = asyncio.create_task(ordinary_http())
        async with asyncio.timeout(10):
            while not connector._waiters:
                await asyncio.sleep(0.01)
        assert not unrelated.done()
        turns[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await turns[0]
        assert await asyncio.wait_for(unrelated, 10) == "UNRELATED-HEALTHY"
        assert not turns[1].done(), "Remaining Scrape still owns its slow response"
        release.set()
        assert _speech(await asyncio.wait_for(turns[1], 10)) == "Pool tool recovered"
        assert _speech(await asyncio.wait_for(say("c"), 10)) == "Pool tool recovered"
        assert await ordinary_http() == "UNRELATED-HEALTHY"
        assert not session.closed and not connector._acquired and not connector._waiters
        record(
            stress_trace,
            "summary",
            layer="provider-wire",
            shared_http_pool_pressure_cases=1,
            shared_http_waiter_recoveries=1,
            recovery_conversations=2,
        )
    finally:
        release.set()
        for task in [*turns, unrelated]:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in [*turns, unrelated] if task is not None),
            return_exceptions=True,
        )
        for connector, limit in original_limits.items():
            connector._limit = limit
        await runner.cleanup()


@pytest.mark.parametrize("api_mode", API_MODES)
async def test_rest_request_templates_keep_per_invocation_arguments(
    hass, monkeypatch, socket_enabled, stress_trace, api_mode
):
    from copy import deepcopy
    import httpx

    from tests_real_ha.test_provider_wire_e2e import _raw_client

    seen = []
    overlap = asyncio.Event()
    release = asyncio.Event()
    cities = ["Carlow", "Dublin", "Galway", "Cork"]

    async def receive(request):
        seen.append((request.match_info["city"], request.headers["X-City"], dict(request.query), await request.text()))
        if request.match_info["city"] in {"Galway", "Cork"}:
            if len(seen) == 4:
                overlap.set()
            await release.wait()
        return web.Response(text="Received")

    app = web.Application()
    app.router.add_post("/{city}", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    tasks = []
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        tool = {
            "spec": {"name": "scoped_rest", "description": "Send scoped city", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
            "function": {"type": "rest", "method": "POST", "resource_template": url + "/{{ city }}", "payload_template": "body={{ city }}", "headers": {"X-City": "{{ city.upper() }}"}, "params": {"city": "{{ city.lower() }}"}},
        }
        entry = _make_entry("REST invocation scope", include_ai_task=False, conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]})
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        saved = deepcopy(agent.subentry.data[CONF_FUNCTION_TOOLS])
        requests = []

        async def send(request, *args, **kwargs):
            body = json.loads(request.content)
            requests.append(body)
            city = next(city for city in cities if f"scope {city}" in json.dumps(body))
            messages = body.get("messages", body.get("input", []))
            complete = any(item.get("role") == "tool" or item.get("type") == "function_call_output" for item in messages)
            if api_mode == API_MODE_RESPONSES:
                reply = _responses_sse_text("Received city") if complete else _responses_sse_tool_call(f"scope-{city}", "scoped_rest", {"city": city})
            else:
                reply = _chat_sse_text("Received city") if complete else _chat_sse_tool_call(f"scope-{city}", "scoped_rest", {"city": city})
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=reply, request=request)

        monkeypatch.setattr(_raw_client(agent)._client, "send", send)

        async def say(city):
            return await conversation.async_converse(hass=hass, text=f"scope {city}", conversation_id=None, context=Context(), language="en", agent_id=entry.entry_id)

        for city in cities[:2]:
            assert _speech(await say(city)) == "Received city"
        tasks = [asyncio.create_task(say(city)) for city in cities[2:]]
        await asyncio.wait_for(overlap.wait(), 10)
        release.set()
        assert all(_speech(result) == "Received city" for result in await asyncio.gather(*tasks))
        assert sorted(seen) == sorted((city, city.upper(), {"city": city.lower()}, f"body={city}") for city in cities)
        assert agent.subentry.data[CONF_FUNCTION_TOOLS] == saved
        assert len(requests) == 8
        record(stress_trace, "summary", rest_scoped_requests=4, rest_overlapping_requests=2)
    finally:
        release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("route", ["direct", "composite", "rule"])
@pytest.mark.parametrize("fault", ["disconnect", "timeout"])
async def test_rest_transport_failure_stops_dependent_actions(
    hass, monkeypatch, socket_enabled, stress_trace, api_mode, route, fault
):
    from custom_components.extended_openai_conversation_responses.const import (
        DOMAIN,
        SERVICE_CALL_FUNCTION,
    )
    from tests_real_ha.test_request_rules_script_semantics import _local, _record_action

    healthy = False
    release = asyncio.Event()
    attempts = []
    effects = []

    async def remote(request):
        attempts.append("healthy" if healthy else fault)
        if healthy:
            return web.Response(text="HEALTHY_REST")
        if fault == "disconnect":
            request.transport.close()
        else:
            await release.wait()
        return web.Response(text="UNUSABLE")

    async def marker(call):
        effects.append(call.data["message"])

    hass.services.async_register("rule_probe", "record", marker)
    app = web.Application()
    app.router.add_post("/probe", remote)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/probe"
        config = {"type": "rest", "method": "POST", "resource": url, "timeout": 1}
        if route == "composite":
            config = {
                "type": "composite",
                "sequence": [
                    {"type": "script", "sequence": [_record_action("before")]},
                    config,
                    {"type": "script", "sequence": [_record_action("after")]},
                ],
            }
        tool = {
            "spec": {
                "name": "transport_probe",
                "description": "Probe transport",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": config,
        }
        entry = _make_entry(
            "REST failure consequences",
            include_ai_task=False,
            conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]},
        )
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        if route == "rule":
            await agent._request_rules.async_create(
                _local(
                    [
                        _record_action("before"),
                        {
                            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                            "data": {"function": "transport_probe", "arguments": {}},
                        },
                        _record_action("after"),
                    ]
                )
            )
            replies = []
        else:
            replies = _provider_replies(
                api_mode, "transport-failure", "transport_probe", "Failure handled"
            )
        wire = _install_wire(monkeypatch, agent, replies)

        async def say():
            return await conversation.async_converse(
                hass=hass,
                text="run rule" if route == "rule" else "Probe transport",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
            )

        healthy = True
        healthy_wire = _install_wire(
            monkeypatch,
            agent,
            []
            if route == "rule"
            else _provider_replies(
                api_mode, "transport-before", "transport_probe", "Healthy before fault"
            ),
        )
        before = await say()
        assert _speech(before) == (
            "Done" if route == "rule" else "Healthy before fault"
        )
        assert effects == ([] if route == "direct" else ["before", "after"])
        assert attempts == ["healthy"]
        assert len(healthy_wire.requests) == (0 if route == "rule" else 2)
        healthy = False
        wire = _install_wire(monkeypatch, agent, replies)
        result = await say()
        if route == "rule":
            assert result.response.error_code is not None
            assert result.response.as_dict()["speech"]["plain"]["speech"] == "Failed safely"
            assert not agent._usage.runs[-1].successful
        else:
            assert _speech(result) == "Failure handled"
            outcome = _provider_result(wire.requests[1], api_mode, "transport-failure")
            assert outcome["status"] == "error", outcome
        assert effects == ([] if route == "direct" else ["before", "after", "before"])
        assert attempts == ["healthy", fault], (
            "An uncertain remote operation must not be replayed"
        )
        healthy = True
        release.set()
        wire = _install_wire(
            monkeypatch,
            agent,
            []
            if route == "rule"
            else _provider_replies(
                api_mode, "transport-recovery", "transport_probe", "Recovered transport"
            ),
        )
        result = await say()
        assert _speech(result) == ("Done" if route == "rule" else "Recovered transport")
        assert effects == (
            []
            if route == "direct"
            else ["before", "after", "before", "before", "after"]
        )
        assert attempts == ["healthy", fault, "healthy"]
        final = (
            _responses_sse_text if api_mode == API_MODE_RESPONSES else _chat_sse_text
        )
        independent_wire = _install_wire(
            monkeypatch, agent, [final("Independent healthy request")]
        )
        independent = await conversation.async_converse(
            hass=hass,
            text="Independent conversation",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(independent) == "Independent healthy request"
        assert len(independent_wire.requests) == 1
        assert attempts == ["healthy", fault, "healthy"]
        record(
            stress_trace,
            "summary",
            rest_transport_failure_cases=1,
            rest_transport_recoveries=1,
            rest_blocked_dependent_actions=int(route != "direct"),
        )
    finally:
        release.set()
        await runner.cleanup()


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("status,body", [(204, ""), (404, "Received error body")])
async def test_rest_completed_empty_and_http_error_responses_remain_data(
    hass, monkeypatch, socket_enabled, stress_trace, api_mode, status, body
):
    async def remote(request):
        return web.Response(status=status, text=body)

    app = web.Application()
    app.router.add_get("/probe", remote)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/probe"
        tool = {
            "spec": {
                "name": "response_probe",
                "description": "Probe response",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "rest", "resource": url},
        }
        entry = _make_entry(
            "REST received responses",
            include_ai_task=False,
            conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]},
        )
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        wire = _install_wire(
            monkeypatch,
            agent,
            _provider_replies(
                api_mode, "received-response", "response_probe", "Received response"
            ),
        )
        result = await conversation.async_converse(
            hass=hass,
            text="Read response",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(result) == "Received response"
        assert _provider_result(wire.requests[1], api_mode, "received-response") == (
            body
            if status == 204
            else {"status": "error", "error": "REST request failed: HTTP 404 Not Found"}
        )
        record(stress_trace, "summary", rest_received_response_controls=1)
    finally:
        await runner.cleanup()
