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
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
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


@pytest.mark.parametrize("kind", REMAINING_TYPES)
async def test_remaining_function_type_executes_on_provider_wire(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    socket_enabled: Any,
    tmp_path: Path,
    stress_trace: list[dict],
    kind: str,
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
                CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
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
            [
                _chat_sse_tool_call(call_id, tool_name, {}),
                _chat_sse_text("Fixture complete"),
            ],
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
        assert len(wire.requests) == 2
        assert tool_name in _tool_names(
            wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS
        )
        tool_message = next(
            message
            for message in wire.requests[1]["body"]["messages"]
            if message.get("role") == "tool" and message.get("tool_call_id") == call_id
        )
        result = json.loads(tool_message["content"])["result"]
        if isinstance(result, dict):
            assert "error" not in result, result
        if kind == "rest":
            assert "EOAI_REST_WIRE" in str(result)
            assert calls == ["rest"]
        elif kind == "scrape":
            assert "EOAI_SCRAPE_WIRE" in str(result)
            assert calls == ["scrape"]
        elif kind == "composite":
            assert result == "COMPOSITE-FIRST-SECOND"
        elif kind == "sqlite":
            assert "EOAI_SQLITE_WIRE" in str(result)
        elif kind == "bash":
            assert result["exit_code"] == 0
            assert result["stdout"] == "EOAI_BASH_WIRE"
        elif kind == "read_file":
            assert result["content"] == "EOAI_READ_WIRE"
        elif kind == "write_file":
            assert marker.read_text(encoding="utf-8") == "EOAI_WRITE_WIRE"
            assert result["success"] is True
        elif kind == "edit_file":
            assert marker.read_text(encoding="utf-8") == "EOAI_EDIT_WIRE"
            assert result["success"] is True
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
