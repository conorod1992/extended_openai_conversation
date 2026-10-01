"""Exercise every remaining built-in Function type through public Assist and SDK wire."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from aiohttp import web
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_FUNCTION_TOOLS,
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
            assert result == "404: Not Found"
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
    assert result == {"error": f"File not found: {missing}"}
    assert side_effects.read_text(encoding="utf-8").splitlines() == ["first"]
    record(stress_trace, "composite_partial_failure", mode=api_mode, side_effects=1)


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("mixed", [False, True], ids=["safe-overlap", "mixed-serial"])
async def test_public_assist_multicall_dispatch_concurrency(
    hass, monkeypatch, stress_trace, api_mode, mixed
):
    """SDK parsing and public Assist retain safe overlap and serial side effects."""
    import asyncio

    from pytest_homeassistant_custom_component.common import MockUser

    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )
    from tests.test_openai_sdk_wire import _chat_chunk, _sse
    from tests_real_ha.test_function_execution_composition import (
        _responses_sse_tool_calls,
    )

    owner = MockUser(
        id="parallel-owner", name="Parallel owner", is_owner=True
    ).add_to_hass(hass)
    started = {name: asyncio.Event() for name in ("a", "b")}
    release = {name: asyncio.Event() for name in ("a", "b")}
    completed = {name: asyncio.Event() for name in ("a", "b")}
    effects = []
    native = get_function("native")
    original = native.get_user_from_user_id

    async def gated(*args):
        marker = args[2]["marker"]
        started[marker].set()
        await release[marker].wait()
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
    entry = _make_entry(
        "Assist concurrency",
        include_ai_task=False,
        conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: tools},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    calls = [
        (f"call-{marker}", f"probe_{marker}", {"marker": marker})
        for marker in ("a", "b")
    ]
    first = (
        _responses_sse_tool_calls(calls)
        if api_mode == API_MODE_RESPONSES
        else _sse(
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
    )
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
            release["b"].set()
            await asyncio.wait_for(completed["b"].wait(), 10)
        release["a"].set()
        result = await asyncio.wait_for(turn, 10)
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
            "public_assist_concurrency",
            api_mode=api_mode,
            mixed=mixed,
            function_executions=2,
            conversations=1,
            safe_overlap=int(not mixed),
            assist_safe_overlap=int(not mixed),
            assist_mixed_serial=int(mixed),
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
    "fault", ["length", "chunked", "trickle", "disconnect", "encoding"]
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
        if failed.response.error_code is None:
            assert len(wire.requests) == 2
            value = _provider_result(wire.requests[1], api_mode, "call-fault")
            assert value is None or (
                isinstance(value, dict) and value.get("status") == "error"
            ), value
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
            "remote_resource_boundary",
            api_mode=api_mode,
            kind=kind,
            fault=fault,
            remote_resource_recovery_cases=1,
            remote_failures=1,
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
        "composite_resource_rejection",
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
            "composite_cancellation",
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
