"""Prepared file identities stay precise through interrupted Composite executions."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.functions import (
    file as files,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    replay_safety,
)
from tests_real_ha.test_cross_feature_acceptance import _agent, _say
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("operation", ["write_file", "edit_file"])
@pytest.mark.parametrize("failure", ["partial", "uncertain"])
async def test_file_recovery_matches_effects_after_partial_or_uncertain_execution(
    hass, monkeypatch, mode, operation, failure
):
    root = Path(hass.config.config_dir) / DOMAIN
    root.mkdir(parents=True, exist_ok=True)
    (root / "sub").mkdir()
    source = root / "source.txt"
    distinct = root / "different.txt"
    source.write_text("seed", encoding="utf-8")
    distinct.write_text("seed", encoding="utf-8")
    backend = {
        "type": operation,
        "path": "{{ filename | default('source.txt') }}",
        **(
            {"content": "{{ text | default('seed!') }}"}
            if operation == "write_file"
            else {
                "old_text": "{{ old | default('seed') }}",
                "new_text": "{{ new | default('seed!') }}",
            }
        ),
    }
    implementation = (
        {
            "type": "composite",
            "sequence": [
                backend,
                {
                    "type": "edit_file",
                    "path": "absent.txt",
                    "old_text": "not present",
                    "new_text": "corrected",
                },
            ],
        }
        if failure == "partial"
        else backend
    )
    other_type = (
        {
            "type": "edit_file",
            "path": "source.txt",
            "old_text": "seed!",
            "new_text": "type changed",
        }
        if operation == "write_file"
        else {"type": "write_file", "path": "source.txt", "content": "seed!"}
    )
    tools = [
        {
            "spec": {
                "name": name,
                "description": "Mutate a local file",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string"}
                        for key in ("filename", "text", "old", "new")
                    },
                },
            },
            "function": config,
        }
        for name, config in (
            ("mutate", implementation),
            ("mutate_alias", backend),
            ("other_type", other_type),
        )
    ]
    agent = await _agent(
        hass,
        api_mode=mode,
        **{CONF_FUNCTION_TOOLS: tools},
        # Project backend failures as tool results, then fail their provider
        # continuation instead of stopping at the stricter execution boundary.
        function_tool_error_recovery=False,
    )
    writes = []
    original = files._atomic_replace_text

    def interrupted_writer(path, content, **kwargs):
        result = original(path, content, **kwargs)
        writes.append((path, content))
        if failure == "uncertain" and len(writes) == 1:
            raise OSError("Writer acknowledgement lost after atomic replacement")
        return result

    monkeypatch.setattr(files, "_atomic_replace_text", interrupted_writer)
    call = _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call
    text = _responses_sse_text if mode == "responses" else _chat_sse_text
    continuation_failure = (
        400,
        {"error": {"message": "Continuation failed", "type": "invalid_request_error"}},
    )
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    wire = _install_wire(
        monkeypatch, agent, [call("first", "mutate", {}), continuation_failure]
    )
    failed = await _say(hass, agent, "mutate", seed.conversation_id)
    assert failed.response.error_code is not None
    assert len(wire.requests) == 2  # The SDK reached the failed continuation.
    assert source.read_text(encoding="utf-8") == "seed!"
    assert len(writes) == 1

    # The edit's search string still matches seed!, so an unguarded retry would
    # really mutate the file a second time. Different path spelling/defaults
    # and an alias that bypasses the Composite wrapper must retain that guard.
    equivalent = {
        "filename": "./sub/../source.txt",
        **(
            {"text": "seed!"}
            if operation == "write_file"
            else {"old": "seed", "new": "seed!"}
        ),
    }
    for index, name in enumerate(("mutate", "mutate_alias")):
        _install_wire(monkeypatch, agent, [call(f"retry-{index}", name, equivalent)])
        blocked = await _say(hass, agent, "retry", failed.conversation_id)
        assert blocked.response.error_code is not None
        assert source.read_text(encoding="utf-8") == "seed!"
        assert len(writes) == 1

    # A distinct canonical path through the same tool is safe even if its
    # earlier Composite child or native writer left an unresolved outcome.
    _install_wire(
        monkeypatch,
        agent,
        [
            call("different", "mutate", {"filename": "different.txt"}),
            continuation_failure,
        ],
    )
    changed = await _say(hass, agent, "mutate another file", failed.conversation_id)
    assert changed.response.error_code is not None
    assert distinct.read_text(encoding="utf-8") == "seed!"
    assert len(writes) == 2

    # Operation type is part of the prepared identity, independently of owner.
    _install_wire(
        monkeypatch, agent, [call("other-type", "other_type", {}), continuation_failure]
    )
    different_type = await _say(
        hass, agent, "use another operation", failed.conversation_id
    )
    assert different_type.response.error_code is not None
    assert len(writes) == 3

    # A successful provider acknowledgement permits an intentional repetition.
    _install_wire(monkeypatch, agent, [text("Acknowledged")])
    acknowledged = await _say(hass, agent, "acknowledge", failed.conversation_id)
    assert acknowledged.response.error_code is None
    _install_wire(
        monkeypatch, agent, [call("intentional", "mutate", {}), continuation_failure]
    )
    await _say(hass, agent, "repeat intentionally", failed.conversation_id)
    assert len(writes) == 4


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_repeated_cancellation_settles_usage_and_preserves_file_recovery(
    hass, monkeypatch, mode
):
    root = Path(hass.config.config_dir) / DOMAIN
    root.mkdir(parents=True, exist_ok=True)
    target = root / "cancelled.txt"
    agent = await _agent(
        hass,
        api_mode=mode,
        **{
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": "write_note",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "write_file",
                        "path": "cancelled.txt",
                        "content": "written once",
                    },
                }
            ]
        },
    )
    writes = []
    original = files._atomic_replace_text

    def counted(path, content, **kwargs):
        writes.append((path, content))
        return original(path, content, **kwargs)

    monkeypatch.setattr(files, "_atomic_replace_text", counted)
    call = _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call
    text = _responses_sse_text if mode == "responses" else _chat_sse_text
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    manager = agent._usage
    assert manager is not None
    continuation_started = asyncio.Event()
    requests = []

    async def send(request, *args, **kwargs):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=call("original", "write_note", {}),
                request=request,
            )
        assert target.read_text(encoding="utf-8") == "written once"
        continuation_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    task = asyncio.create_task(_say(hass, agent, "write", seed.conversation_id))
    await asyncio.wait_for(continuation_started.wait(), 5)
    requests_before = manager.totals.api_request_count
    failed_before = manager.totals.failed_request_count
    await manager._lock.acquire()
    try:
        task.cancel()
        async with asyncio.timeout(5):
            while not manager._lock._waiters:
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        manager._lock.release()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert manager.totals.api_request_count == requests_before + 1
    assert manager.totals.failed_request_count == failed_before + 1
    failed_request = manager.requests[-1]
    failed_run = manager.runs[-1]
    assert failed_request.run_id == failed_run.run_id
    assert failed_request.request_stage == "after_tool"
    assert failed_request.successful is False
    assert failed_request.error_type == "CancelledError"
    assert failed_run.request_count == 2
    assert failed_run.failed_request_count == 1
    assert failed_run.error_type == "CancelledError"
    assert replay_safety(agent).effects[seed.conversation_id]
    assert agent._execution_effect_receipts
    assert writes == [(target, "written once")]

    _install_wire(monkeypatch, agent, [call("retry", "write_note", {})])
    blocked = await _say(hass, agent, "retry", seed.conversation_id)
    assert blocked.response.error_code is not None
    assert target.read_text(encoding="utf-8") == "written once"
    assert writes == [(target, "written once")]
    _install_wire(monkeypatch, agent, [text("Acknowledged")])
    acknowledged = await _say(hass, agent, "acknowledge", seed.conversation_id)
    assert acknowledged.response.error_code is None
    assert not replay_safety(agent).effects.get(seed.conversation_id)
    assert not agent._execution_effect_receipts
