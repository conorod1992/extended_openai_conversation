"""Regression coverage for audited execution and HTTP boundaries."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zlib

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolCall,
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.functions import file, web
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    _signature,
    record_dispatch,
    remember_unacknowledged_calls,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)


@pytest.mark.parametrize("search", ["", '{{ missing | default("") }}'])
async def test_empty_search_never_edits_empty_file(hass, search):
    tool = file.EditFileFunction()
    path = tool.get_working_dir(hass) / "empty.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    config = {key: Template(value, hass) for key, value in {
        "path": str(path), "old_text": search, "new_text": "unexpected insertion",
    }.items()}
    result = await tool.execute(hass, config, {}, None, [])
    assert "empty" in result["error"]
    assert path.read_text(encoding="utf-8") == ""


def test_raw_deflate_with_zlib_like_first_byte():
    body = bytes.fromhex("08 01 00 fe ff 41 01 00 00 ff ff")
    assert zlib.decompress(body, -zlib.MAX_WBITS) == b"A"
    assert web._decode_compressed_body(body, "deflate", 1) == b"A"
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_compressed_body(body, "deflate", 0)


@pytest.mark.parametrize("content_size", [True, False])
def test_compat_zstd_bounds_and_requires_complete_frames(monkeypatch, content_size):
    zstandard = pytest.importorskip("zstandard")
    monkeypatch.setattr(web, "_ZSTD_COMPAT", zstandard)
    compressor = zstandard.ZstdCompressor(write_content_size=content_size)
    first, second = compressor.compress(b"first"), compressor.compress(b"second")
    assert web._decode_zstd(first + second, 11) == b"firstsecond"
    for body in (first[:-1], first + second[:-1], first + b"garbage"):
        with pytest.raises(Exception, match="compressed response"):
            web._decode_zstd(body, 11)
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_zstd(compressor.compress(b"x" * 1_000_000), 64)


@pytest.mark.parametrize("numeric_type", ["number", ["integer", "null"]])
async def test_numeric_retry_cannot_dispatch_again(hass, numeric_type):
    entity, log = _entity(hass), _chat_log(hass)
    tool = _tool("effect", parameters={"type": "object", "properties": {
        "payload": {"type": "array", "items": {"type": numeric_type}},
    }})
    effects = []
    async def execute(definition, tool_input, *_):
        record_dispatch(entity, tool_input, definition)
        effects.append(deepcopy(tool_input.tool_args))
        return _result(entity, tool_input)
    entity._execute_function_tool.side_effect = execute
    before = {id(content) for content in log.content}
    call = _call("original", "effect", {"payload": [1]})
    _retain_calls(log, [call])
    await async_execute_tool_exchange(entity, log, [call], [tool], FunctionCallBudget(10), None, [], recovery_state=ToolRecoveryState(enabled=True))
    remember_unacknowledged_calls(entity, log, before)
    retry = _call("fresh", "effect", {"payload": [1.0]})
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(entity, log, [retry], [tool], FunctionCallBudget(10), None, [], recovery_state=ToolRecoveryState(enabled=True))
    assert effects == [{"payload": [1]}]


def test_replay_preserves_precision_and_other_scalar_types():
    spec = {"parameters": {"type": "object"}}
    def signature(value):
        return _signature(_call("id", "effect", {"value": value}), spec)
    assert signature(2**53 + 1) != signature(float(2**53 + 1))
    assert signature(True) != signature(1)
    assert signature("1") != signature(1)
    assert signature(None) != signature(0)
    assert signature(-0.0) == signature(0)


def test_service_target_overrides_only_duplicate_selectors():
    assert ha_actions._target_selection(
        {"entity_id": "counter.hidden", "area_id": "kitchen"},
        {"entity_id": "counter.exposed"},
    ) == {"entity_id": ["counter.exposed"], "area_id": ["kitchen"]}
    assert "entity_id" not in ha_actions._target_selection({"entity_id": "counter.hidden"}, {"entity_id": []})


async def test_nullable_delay_schedules_and_keeps_restart_arguments(hass):
    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.data.setdefault(DOMAIN, {})[DATA_DELAYED_TOOL_MANAGER] = manager
    entity = SimpleNamespace(hass=hass, entity_id="conversation.audit",
        entry=SimpleNamespace(entry_id="entry"), subentry=SimpleNamespace(subentry_id="agent"),
        should_run_in_background=lambda delay: bool(delay))
    args = {"delay": {"hours": None, "minutes": 5, "seconds": None}}
    tool = _tool("effect", parameters={"type": "object", "properties": {"delay": {
        "type": "object", "properties": {key: {"type": ["integer", "null"]} for key in args["delay"]},
        "required": list(args["delay"]),
    }}, "required": ["delay"]})
    await ExtendedOpenAIBaseLLMEntity._execute_function_tool(entity, tool, _call("schedule", "effect", args), None, [])
    record = next(iter(manager._records.values()))
    restored = DelayedToolCall.from_dict(record.as_dict())
    assert restored.arguments == args
    from homeassistant.util import dt as dt_util
    assert (dt_util.parse_datetime(restored.due_at) - dt_util.parse_datetime(restored.created_at)).total_seconds() == 300


@pytest.mark.parametrize("name", ["effect", "load_function_groups"])
@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
async def test_finalizer_retry_rejects_unadvertised_calls(hass, name, api_mode):
    from homeassistant.components import conversation
    from tests.test_responses_api import (
        FakeStream,
        _completed_event,
        _event,
        _function_call,
    )
    message = SimpleNamespace(type="message")
    unexpected = _function_call(name, "{}", "unexpected")
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=[
        FakeStream([_event("response.output_item.added", item=message), _event("response.output_text.delta", delta="Draft"), _event("response.output_item.done", item=message), _completed_event()]),
        FakeStream([_event("response.output_item.added", item=unexpected), _event("response.output_item.done", item=unexpected), _completed_event()]),
    ])))
    if api_mode == "chat_completions":
        def chunk(text, calls, reason):
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(
                delta=SimpleNamespace(content=text, refusal=None, tool_calls=calls), finish_reason=reason,
            )])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=[
            FakeStream([chunk("Draft", None, "stop")]),
            FakeStream([chunk(None, [SimpleNamespace(index=0, id="unexpected", function=SimpleNamespace(name=name, arguments="{}"))], "tool_calls")]),
        ]))))
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(data={"chat_model": "gpt-4o" if api_mode == "chat_completions" else "gpt-5.6-luna", "api_mode": api_mode})
    entity.hass, entity.entity_id = hass, "conversation.audit"
    entity._execute_function_tool = AsyncMock()
    loader = AsyncMock()
    log = conversation.ChatLog(hass, "audit")
    log.async_add_user_content(conversation.UserContent(content="Run"))
    with pytest.raises(HomeAssistantError, match="not advertised"):
        await entity._async_handle_chat_log(log, [_tool("effect")], [], conditional_continue=True, function_group_loader=loader)
    entity._execute_function_tool.assert_not_awaited()
    loader.assert_not_called()


def test_raw_deflate_can_have_a_complete_valid_zlib_header():
    # Non-final stored block with ignored padding, LEN=156, NLEN=~156.
    body = bytes.fromhex("78 9c 00 63 ff") + b"A" * 156 + bytes.fromhex("01 00 00 ff ff")
    assert int.from_bytes(body[:2], "big") % 31 == 0
    assert zlib.decompress(body, -zlib.MAX_WBITS) == b"A" * 156
    assert web._decode_compressed_body(body, "deflate", 156) == b"A" * 156
    with pytest.raises(HomeAssistantError, match="safety limit"):
        web._decode_compressed_body(body, "deflate", 155)


async def test_nullable_delay_survives_real_store_restart(hass, monkeypatch):
    from tests.test_delayed_tool_recovery_round_trip import _setup_without_waiters, _entity
    from custom_components.extended_openai_conversation_responses.function_execution import (
        validate_function_arguments, split_legacy_execution_delay,
    )
    first, _ = await _setup_without_waiters(hass, monkeypatch)
    args = {"delay": {"hours": None, "minutes": 5, "seconds": None}}
    tool = _tool("effect", parameters={"type": "object", "properties": {"delay": {
        "type": "object", "properties": {key: {"type": ["integer", "null"]} for key in args["delay"]},
        "required": list(args["delay"]),
    }}, "required": ["delay"]})
    record = await first.async_schedule(
        _entity(), "effect", args, None, function_tool=tool, execution_delay={"minutes": 5}
    )
    first._handle_stop()
    second, arm = await _setup_without_waiters(hass, monkeypatch)
    try:
        restored = second._records[record.call_id]
        assert restored.arguments == args
        arm.assert_called_once_with(record.call_id)
        validated = validate_function_arguments(tool["spec"], restored.arguments)
        assert split_legacy_execution_delay(tool["spec"], validated) == ({}, {"minutes": 5})
    finally:
        second._handle_stop()
