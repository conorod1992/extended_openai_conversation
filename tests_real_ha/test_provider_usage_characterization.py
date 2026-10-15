"""Usage policies through public HA processing, SDK SSE and native Stores."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    _LOCAL_ESTIMATE_DETAIL,
    _PARTIAL_PROVIDER_USAGE_DETAIL,
)
from custom_components.extended_openai_conversation_responses.debug import (
    get_debug_manager,
)
from tests.test_provider_usage_characterization import _provider_expected, _reported
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _say,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


def _body(mode, kind, limited, *, prior_full=False, failed=False):
    raw = _reported(kind)
    source = (
        _chat_sse_text("Usage characterization")
        if mode == API_MODE_CHAT_COMPLETIONS
        else _responses_sse_text("Usage characterization")
    )
    events = [
        json.loads(part.removeprefix("data: "))
        for part in source.decode().split("\n\n")
        if part and part != "data: [DONE]"
    ]
    if mode == API_MODE_CHAT_COMPLETIONS:

        def usage_chunk(value):
            value = (
                None
                if value is None
                else {
                    key.replace("input_tokens", "prompt_tokens").replace(
                        "output_tokens", "completion_tokens"
                    ): item
                    for key, item in value.items()
                }
            )
            return {**events[0], "choices": [], "usage": value}

        events[0]["choices"][0]["finish_reason"] = "length" if limited else "stop"
        if prior_full:
            events.append(usage_chunk(_reported("full")))
        events.append(usage_chunk(raw))
    else:
        terminal = events[-1]
        terminal["response"]["usage"] = raw
        if limited:
            terminal["type"] = "response.incomplete"
            terminal["response"].update(
                status="incomplete", incomplete_details={"reason": "max_output_tokens"}
            )
        if failed:
            terminal["type"] = "response.failed"
            terminal["response"].update(
                status="failed",
                error={"code": "server_error", "message": "Provider failed"},
            )
        if prior_full:
            events.insert(
                -1,
                {
                    "type": "response.usage",
                    "usage": _reported("full"),
                    "sequence_number": 3,
                },
            )
            terminal["sequence_number"] = 4
    encoded = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (
        encoded + ("data: [DONE]\n\n" if mode == API_MODE_CHAT_COMPLETIONS else "")
    ).encode()


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("limited", [False, True], ids=["completed", "length"])
@pytest.mark.parametrize(
    "kind",
    ["missing", "zero", "full", "input", "output", "total", "reasoning", "cached"],
)
async def test_public_stream_usage_matches_accounting_storage_and_diagnostics(
    hass, monkeypatch, mode, limited, kind
):
    agent = await _agent(hass, mode)
    manager = agent._usage
    debug = get_debug_manager(hass, agent.entry.entry_id, agent.subentry.subentry_id)
    debug.configure(enabled=True)
    captured = []
    original = manager.async_record_request

    async def record(**kwargs):
        captured.append(deepcopy(kwargs["usage"]))
        await original(**kwargs)

    monkeypatch.setattr(manager, "async_record_request", record)
    wire = _install_wire(monkeypatch, agent, [_body(mode, kind, limited)])
    result = await _say(hass, agent)
    assert (result.response.error_code is not None) == limited
    assert len(wire.requests) == len(captured) == len(manager.requests) == 1
    expected = _provider_expected(kind)
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        assert getattr(manager.requests[0], key) == getattr(expected, key)
        assert getattr(manager.totals, key) == getattr(expected, key)
        assert debug.summaries()[0][key] == getattr(expected, key)
    assert manager.requests[0].successful == (not limited)
    context = captured[0]
    retains_estimate = kind in {
        "missing",
        "zero",
        "output",
        "total",
        "reasoning",
        "cached",
    }
    assert (_LOCAL_ESTIMATE_DETAIL in context.details) == retains_estimate
    if retains_estimate:
        assert context.input_tokens == context.details[_LOCAL_ESTIMATE_DETAIL] > 0
        assert (_PARTIAL_PROVIDER_USAGE_DETAIL in context.details) == (
            kind not in {"missing", "zero"}
        )
    else:
        assert context.input_tokens == expected.input_tokens

    # Read the real HA Store snapshots, rather than just the live aggregate objects.
    aggregates = await manager._daily_storage.async_load()
    details = await manager._detail_storage.async_load()
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        assert aggregates["totals"][key] == getattr(expected, key)
        assert details["requests"][0][key] == getattr(expected, key)
    assert _LOCAL_ESTIMATE_DETAIL not in json.dumps(details)


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("kind", ["missing", "zero", "output"])
@pytest.mark.parametrize("limited", [False, True], ids=["completed", "length"])
async def test_later_empty_terminal_preserves_genuine_snapshot(
    hass, monkeypatch, mode, kind, limited
):
    agent = await _agent(hass, mode)
    _install_wire(monkeypatch, agent, [_body(mode, kind, limited, prior_full=True)])
    result = await _say(hass, agent)
    assert (result.response.error_code is not None) == limited
    expected = _provider_expected("full" if kind in {"missing", "zero"} else kind)
    totals = await agent._usage._daily_storage.async_load()
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        assert totals["totals"][key] == getattr(expected, key)


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize(
    "kind",
    ["missing", "zero", "full", "input", "output", "total", "reasoning", "cached"],
)
async def test_failed_responses_persists_provider_counts_and_next_turn_recovers(
    hass, monkeypatch, kind
):
    agent = await _agent(hass, API_MODE_RESPONSES)
    manager = agent._usage
    captured = []
    original = manager.async_record_request

    async def record(**kwargs):
        captured.append(deepcopy(kwargs["usage"]))
        await original(**kwargs)

    monkeypatch.setattr(manager, "async_record_request", record)
    _install_wire(
        monkeypatch,
        agent,
        [
            _body(API_MODE_RESPONSES, kind, False, failed=True),
            _body(API_MODE_RESPONSES, "missing", False),
        ],
    )
    failed = await _say(hass, agent)
    assert failed.response.error_code is not None
    assert (_LOCAL_ESTIMATE_DETAIL in captured[0].details) == (
        kind not in {"full", "input"}
    )
    recovered = await _say(hass, agent)
    assert recovered.response.error_code is None
    expected = _provider_expected(kind)
    aggregates = await manager._daily_storage.async_load()
    details = await manager._detail_storage.async_load()
    assert aggregates["totals"]["failed_request_count"] == 1
    assert aggregates["totals"]["successful_request_count"] == 1
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        assert aggregates["totals"][key] == getattr(expected, key)
        assert details["requests"][0][key] == getattr(expected, key)
        assert details["requests"][1][key] == 0
    assert _LOCAL_ESTIMATE_DETAIL not in json.dumps(details)
