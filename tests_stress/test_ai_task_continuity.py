"""Enhanced AI Task image continuity through the public HA API and SDK wire."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
)
from homeassistant.components import ai_task
from homeassistant.core import Context
from tests_real_ha.test_ai_task_provider_wire import MODES, _task_entity, _text_reply
from tests_real_ha.test_ai_task_runtime import CallerAPI, ContextProbeTool
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_tool_call,
    _raw_client,
    _responses_sse_tool_call,
)
from tests_stress.conftest import record


def _image_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data))
        )

    return b"\x89PNG\r\n\x1a\n" + b"".join(
        [
            chunk(b"IHDR", struct.pack("!IIBBBBB", 2, 1, 8, 2, 0, 0, 0)),
            chunk(b"tEXt", b"Owner\0IMAGE_TASK_OWNER_ALPHA"),
            chunk(b"IDAT", zlib.compress(b"\0\xff\x10\x20\x30\x40\xf0")),
            chunk(b"IEND", b""),
        ]
    )


def _assert_origin_image(body: dict, mode: str, image: bytes) -> None:
    items = body["input" if mode == API_MODE_RESPONSES else "messages"]
    users = [item for item in items if item.get("role") == "user"]
    assert len(users) == 1
    content = users[0]["content"]
    assert isinstance(content, list), (
        "Originating image was lost from the tool continuation"
    )
    text_type = "input_text" if mode == API_MODE_RESPONSES else "text"
    assert (
        next(part["text"] for part in content if part["type"] == text_type)
        == "IMAGE_TASK_OWNER_ALPHA: inspect this image using the caller probe."
    )
    image_type = "input_image" if mode == API_MODE_RESPONSES else "image_url"
    parts = [part for part in content if part["type"] == image_type]
    assert len(parts) == 1
    url = (
        parts[0]["image_url"]
        if mode == API_MODE_RESPONSES
        else parts[0]["image_url"]["url"]
    )
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image


@pytest.mark.parametrize("mode", MODES)
async def test_image_and_owner_survive_caller_tool_continuation_then_isolate_next_task(
    hass,
    monkeypatch,
    tmp_path: Path,
    stress_trace,
    mode: str,
) -> None:
    image = _image_bytes()
    (tmp_path / "alpha-image.png").write_bytes(image)
    hass.config.media_dirs["local"] = str(tmp_path)
    entry, entity_id = await _task_entity(hass, mode)
    probe = ContextProbeTool()
    caller = CallerAPI(hass=hass, id="image-wire-probe", name="Image Wire Probe")
    caller.tools = [probe]
    alpha = MockUser(id="image-task-owner-alpha", name="Image owner Alpha")
    beta = MockUser(id="image-task-owner-beta", name="Independent owner Beta")
    alpha.add_to_hass(hass)
    beta.add_to_hass(hass)
    context = Context(user_id=alpha.id)
    call_id = f"image-continuation-{mode}"
    tool_value = "ALPHA_ASSOCIATED_TOOL_RESULT"
    requests = []

    async def send(request: httpx.Request, *args, **kwargs) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"path": request.url.path, "body": body})
        if len(requests) == 1:
            _assert_origin_image(body, mode, image)
            assert len(body["tools"]) == 1
            tool = body["tools"][0]
            name = (
                tool["name"] if mode == API_MODE_RESPONSES else tool["function"]["name"]
            )
            reply = (
                _responses_sse_tool_call
                if mode == API_MODE_RESPONSES
                else _chat_sse_tool_call
            )
            payload = reply(call_id, name, {"value": tool_value})
        else:
            # A continuation cannot be manufactured without the real caller tool.
            if len(requests) == 2:
                assert len(probe.calls) == 1
                assert probe.calls[0][0].tool_args == {"value": tool_value}
                assert probe.calls[0][1].context is context
            payload = _text_reply(
                mode,
                "Image tool complete"
                if len(requests) == 2
                else "Independent Beta complete",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    raw = _raw_client(SimpleNamespace(_client=entry.runtime_data))
    monkeypatch.setattr(raw._client, "send", send)
    result = await ai_task.async_generate_data(
        hass,
        task_name="Alpha image task",
        entity_id=entity_id,
        instructions="IMAGE_TASK_OWNER_ALPHA: inspect this image using the caller probe.",
        attachments=[
            {
                "media_content_id": "media-source://media_source/local/alpha-image.png",
                "media_content_type": "image/png",
            }
        ],
        llm_api=caller,
        context=context,
    )
    assert result.data == "Image tool complete"
    assert len(probe.calls) == 1
    assert probe.calls[0][0].tool_name == "context_probe"
    assert probe.calls[0][0].tool_args == {"value": tool_value}
    assert probe.calls[0][1].context is context
    assert len(requests) == 2
    continuation = requests[1]["body"]
    if mode == API_MODE_RESPONSES:
        calls = [
            item
            for item in continuation["input"]
            if item.get("type") == "function_call"
        ]
        outputs = [
            item
            for item in continuation["input"]
            if item.get("type") == "function_call_output"
        ]
        assert len(calls) == len(outputs) == 1
        assert calls[0]["call_id"] == outputs[0]["call_id"] == call_id
        serialized = outputs[0]["output"]
    else:
        calls = [item for item in continuation["messages"] if item.get("tool_calls")]
        outputs = [
            item for item in continuation["messages"] if item.get("role") == "tool"
        ]
        assert len(calls) == len(outputs) == 1
        assert calls[0]["tool_calls"][0]["id"] == outputs[0]["tool_call_id"] == call_id
        serialized = outputs[0]["content"]
    assert json.loads(serialized) == {"result": {"echo": tool_value}}
    record(
        stress_trace,
        "caller_tool_continuation",
        mode=mode,
        dispatched_tools=1,
        matched_tool_result=True,
    )
    _assert_origin_image(continuation, mode, image)
    record(stress_trace, "image_continuation", mode=mode, dispatched_tools=1)

    independent = await ai_task.async_generate_data(
        hass,
        task_name="Independent Beta task",
        entity_id=entity_id,
        instructions="INDEPENDENT_TASK_OWNER_BETA",
        context=Context(user_id=beta.id),
    )
    assert independent.data == "Independent Beta complete"
    assert len(requests) == 3 and len(probe.calls) == 1
    endpoint = "/v1/responses" if mode == API_MODE_RESPONSES else "/v1/chat/completions"
    assert [request["path"] for request in requests] == [endpoint] * 3
    final_body = requests[-1]["body"]
    final = json.dumps(final_body)
    assert "INDEPENDENT_TASK_OWNER_BETA" in final
    assert "tools" not in final_body
    for absent in (
        base64.b64encode(image).decode(),
        "IMAGE_TASK_OWNER_ALPHA",
        alpha.id,
        tool_value,
        call_id,
        "function_call_output",
        "image_url",
        "input_image",
    ):
        assert absent not in final
    record(
        stress_trace,
        "summary",
        ai_task_image_continuations=1,
        ai_task_image_isolation_checks=1,
    )


@pytest.mark.parametrize("mode", MODES)
async def test_overlapping_caller_tools_keep_context_and_cancellation_request_local(
    hass,
    monkeypatch,
    stress_trace,
    mode: str,
) -> None:
    """Two overlapping tasks with the same tool name must never share bindings."""
    import voluptuous as vol

    entry, entity_id = await _task_entity(hass, mode)

    class TaggedProbe(ContextProbeTool):
        def __init__(self, tag: str) -> None:
            super().__init__()
            self.tag = tag

        async def async_call(self, hass, tool_input, llm_context):
            self.calls.append((tool_input, llm_context))
            return {"echo": f"{self.tag}:{tool_input.tool_args['value']}"}

    alpha_probe, bravo_probe = TaggedProbe("alpha"), TaggedProbe("bravo")
    alpha_api = CallerAPI(hass=hass, id="overlap-alpha", name="Overlap Alpha")
    bravo_api = CallerAPI(hass=hass, id="overlap-bravo", name="Overlap Bravo")
    alpha_api.tools = [alpha_probe]
    bravo_api.tools = [bravo_probe]
    alpha_user = MockUser(id="overlap-task-alpha", name="Overlap Alpha")
    bravo_user = MockUser(id="overlap-task-bravo", name="Overlap Bravo")
    alpha_user.add_to_hass(hass)
    bravo_user.add_to_hass(hass)
    alpha_context = Context(user_id=alpha_user.id)
    bravo_context = Context(user_id=bravo_user.id)

    admitted = {"alpha": asyncio.Event(), "bravo": asyncio.Event()}
    alpha_continuation = asyncio.Event()
    release_alpha = asyncio.Event()
    requests: list[tuple[str, dict]] = []

    def owner(body: dict) -> str:
        serialized = json.dumps(body)
        matches = [
            name
            for name, marker in (
                ("alpha", "OVERLAP_TASK_ALPHA"),
                ("bravo", "OVERLAP_TASK_BRAVO"),
            )
            if marker in serialized
        ]
        assert len(matches) == 1, serialized
        return matches[0]

    async def send(request: httpx.Request, *args, **kwargs) -> httpx.Response:
        del args, kwargs
        body = json.loads(request.content)
        current = owner(body)
        requests.append((current, body))
        continuation = (
            any(
                item.get("type") == "function_call_output"
                for item in body.get("input", [])
            )
            if mode == API_MODE_RESPONSES
            else any(item.get("role") == "tool" for item in body.get("messages", []))
        )
        if not continuation:
            admitted[current].set()
            other = "bravo" if current == "alpha" else "alpha"
            await asyncio.wait_for(admitted[other].wait(), 5)
            tool = body["tools"][0]
            tool_name = (
                tool["name"] if mode == API_MODE_RESPONSES else tool["function"]["name"]
            )
            call_id = f"overlap-{current}-{mode}"
            reply = (
                _responses_sse_tool_call
                if mode == API_MODE_RESPONSES
                else _chat_sse_tool_call
            )
            payload = reply(call_id, tool_name, {"value": current})
        elif current == "alpha":
            alpha_continuation.set()
            await release_alpha.wait()
            payload = _text_reply(mode, "Alpha must not complete")
        else:
            serialized = json.dumps(body)
            assert "bravo:bravo" in serialized
            assert "alpha:alpha" not in serialized
            payload = _text_reply(mode, '{"owner":"bravo","status":"complete"}')
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    raw = _raw_client(SimpleNamespace(_client=entry.runtime_data))
    monkeypatch.setattr(raw._client, "send", send)

    alpha_task = asyncio.create_task(
        ai_task.async_generate_data(
            hass,
            task_name="Overlapping Alpha",
            entity_id=entity_id,
            instructions="OVERLAP_TASK_ALPHA use the caller probe.",
            llm_api=alpha_api,
            context=alpha_context,
        )
    )
    bravo_task = asyncio.create_task(
        ai_task.async_generate_data(
            hass,
            task_name="Overlapping Bravo",
            entity_id=entity_id,
            instructions="OVERLAP_TASK_BRAVO use the caller probe.",
            structure=vol.Schema(
                {
                    vol.Required("owner"): str,
                    vol.Required("status"): str,
                }
            ),
            llm_api=bravo_api,
            context=bravo_context,
        )
    )
    try:
        await asyncio.wait_for(alpha_continuation.wait(), 10)
        assert len(alpha_probe.calls) == 1
        assert alpha_probe.calls[0][1].context is alpha_context
        assert alpha_probe.calls[0][0].tool_args == {"value": "alpha"}
        alpha_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await alpha_task

        bravo = await asyncio.wait_for(bravo_task, 10)
        assert bravo.data == {"owner": "bravo", "status": "complete"}
        assert len(bravo_probe.calls) == 1
        assert bravo_probe.calls[0][1].context is bravo_context
        assert bravo_probe.calls[0][0].tool_args == {"value": "bravo"}
        assert len(alpha_probe.calls) == len(bravo_probe.calls) == 1

        # A fresh task after cancellation must have no caller tool or prior result.
        async def healthy_send(request: httpx.Request, *args, **kwargs):
            body = json.loads(request.content)
            serialized = json.dumps(body)
            assert "OVERLAP_TASK_HEALTHY" in serialized
            assert "tools" not in body
            assert "alpha:alpha" not in serialized and "bravo:bravo" not in serialized
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_text_reply(mode, "Healthy after overlap"),
                request=request,
            )

        monkeypatch.setattr(raw._client, "send", healthy_send)
        healthy = await ai_task.async_generate_data(
            hass,
            task_name="Healthy after overlap",
            entity_id=entity_id,
            instructions="OVERLAP_TASK_HEALTHY",
        )
        assert healthy.data == "Healthy after overlap"
        assert len(alpha_probe.calls) == len(bravo_probe.calls) == 1
    finally:
        release_alpha.set()
        for task in (alpha_task, bravo_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(alpha_task, bravo_task, return_exceptions=True)

    record(
        stress_trace,
        "summary",
        overlapping_ai_tasks=2,
        overlapping_caller_tool_executions=2,
        ai_task_cancellations_after_effect=1,
        ai_task_overlap_healthy_recoveries=1,
    )
