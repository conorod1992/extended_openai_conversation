"""Tests for content-free Usage input footprint telemetry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening,
)
from custom_components.extended_openai_conversation_responses import input_footprint as footprint
from custom_components.extended_openai_conversation_responses.input_footprint import (
    _baseline_footprint,
    _latest_provider_usage,
    input_footprint_metrics,
)
from custom_components.extended_openai_conversation_responses.management_permissions import (
    wrap_management_permissions,
)


def test_live_footprint_reuses_context_serialization_without_changing_estimate() -> None:
    input_value = [
        {"role": "system", "content": "Use the kitchen light and remember café."},
        {"role": "user", "content": "Turn it on"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "light_on",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    metrics = input_footprint_metrics(input_value, tools)
    input_characters, _ = context_usage_hardening._serialized_characters(input_value)
    tool_characters, _ = context_usage_hardening._serialized_characters(tools)
    characters = input_characters + tool_characters

    assert metrics["input_characters"] == input_characters
    assert metrics["tool_characters"] == tool_characters
    assert metrics["characters"] == characters
    assert metrics["approx_tokens"] == (characters + 3) // 4
    assert metrics["context_safety_estimate_tokens"] == (
        context_usage_hardening.estimate_provider_input_tokens(input_value, tools)
    )


def test_baseline_reports_group_savings_and_approximate_tokens() -> None:
    result = _baseline_footprint(
        {
            "total_character_count": 1000,
            "function_group_savings": {"characters": 240, "percent": 19},
            "notes": ["Conversation history is excluded."],
        }
    )

    assert result["characters"] == 1000
    assert result["approx_tokens"] == 250
    assert result["without_function_groups_characters"] == 1240
    assert result["without_function_groups_approx_tokens"] == 310
    assert result["function_group_savings"] == {
        "characters": 240,
        "approx_tokens": 60,
        "percent": 19,
    }
    assert result["notes"] == ["Conversation history is excluded."]


def test_baseline_clamps_negative_preview_values() -> None:
    result = _baseline_footprint(
        {
            "total_character_count": -10,
            "function_group_savings": {"characters": -20, "percent": -5},
        }
    )

    assert result["characters"] == 0
    assert result["without_function_groups_characters"] == 0
    assert result["function_group_savings"] == {
        "characters": 0,
        "approx_tokens": 0,
        "percent": 0,
    }
    assert result["notes"] == []


def test_provider_usage_is_only_labeled_exact_when_input_tokens_exist() -> None:
    assert _latest_provider_usage(SimpleNamespace(requests=[])) is None

    missing = SimpleNamespace(
        requests=[
            SimpleNamespace(
                timestamp="2026-09-04T12:00:00+00:00",
                input_tokens=0,
                cached_input_tokens=0,
                provider="openai",
                model="gpt-5.6",
                api_mode="responses",
            )
        ]
    )
    assert _latest_provider_usage(missing) is None

    reported = SimpleNamespace(
        requests=[
            SimpleNamespace(
                timestamp="2026-09-04T12:01:00+00:00",
                input_tokens=1234,
                cached_input_tokens=1000,
                provider="openai",
                model="gpt-5.6",
                api_mode="responses",
            )
        ]
    )
    assert _latest_provider_usage(reported) == {
        "timestamp": "2026-09-04T12:01:00+00:00",
        "input_tokens": 1234,
        "cached_input_tokens": 1000,
        "provider": "openai",
        "model": "gpt-5.6",
        "api_mode": "responses",
    }


def test_capture_without_request_state_delegates_to_original(monkeypatch) -> None:
    original = lambda input_value, tools=None: 73
    monkeypatch.setattr(footprint, "_ORIGINAL_ESTIMATE", original, raising=False)

    assert footprint._capture_live_footprint([{"role": "user"}], []) == 73


def test_live_capture_stores_only_content_free_metrics(monkeypatch) -> None:
    hass = SimpleNamespace(data={})
    entity = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-1"),
    )
    state = context_usage_hardening._EstimateState(
        entity=entity,
        function_tools=[],
        function_tools_factory=None,
        conditional_continue=False,
        options={},
    )
    monkeypatch.setattr(
        footprint.dt_util,
        "utcnow",
        lambda: SimpleNamespace(isoformat=lambda: "2026-09-13T10:00:00+00:00"),
    )

    token = context_usage_hardening._CURRENT_ESTIMATE_STATE.set(state)
    try:
        result = footprint._capture_live_footprint(
            [{"role": "user", "content": "private request text"}],
            [{"type": "function", "name": "tool"}],
        )
    finally:
        context_usage_hardening._CURRENT_ESTIMATE_STATE.reset(token)

    stored = hass.data[footprint._LATEST_FOOTPRINTS][("entry-1", "agent-1")]
    assert result > 0
    assert stored["characters"] > 0
    assert stored["input_characters"] > 0
    assert stored["tool_characters"] > 0
    assert stored["captured_at"] == "2026-09-13T10:00:00+00:00"
    assert stored["attachments_excluded"] is True
    assert "context_safety_estimate_tokens" not in stored
    assert "private request text" not in repr(stored)


@pytest.mark.asyncio
async def test_async_input_footprint_combines_preview_latest_and_provider_usage(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    hass = SimpleNamespace(
        data={
            footprint._LATEST_FOOTPRINTS: {
                ("entry-1", "agent-1"): {
                    "characters": 420,
                    "approx_tokens": 105,
                    "captured_at": "2026-09-13T10:00:00+00:00",
                }
            }
        }
    )
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1", data={"chat_model": "gpt-5.6"})
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda _hass, entry_id, subentry_id: (entry, subentry),
    )
    preview = {
        "total_character_count": 800,
        "function_group_savings": {"characters": 200, "percent": 20},
        "notes": ["Fresh request baseline"],
    }
    preview_call = AsyncMock(return_value=preview)
    monkeypatch.setattr(management_ui, "_async_preview_effective_request", preview_call)
    usage = SimpleNamespace(
        requests=[
            SimpleNamespace(
                timestamp="2026-09-13T09:59:00+00:00",
                input_tokens=777,
                cached_input_tokens=700,
                provider="openai",
                model="gpt-5.6",
                api_mode="responses",
            )
        ]
    )
    get_usage = AsyncMock(return_value=usage)
    monkeypatch.setattr(footprint, "async_get_usage", get_usage)

    result = await footprint.async_input_footprint(
        hass,
        "user-1",
        {"entry_id": "entry-1", "subentry_id": "agent-1"},
    )

    assert result["baseline"]["characters"] == 800
    assert result["baseline"]["without_function_groups_characters"] == 1000
    assert result["latest"] == hass.data[footprint._LATEST_FOOTPRINTS][
        ("entry-1", "agent-1")
    ]
    assert result["latest_provider_usage"]["input_tokens"] == 777
    assert "provider billing tokens" in result["notice"]
    preview_call.assert_awaited_once()
    get_usage.assert_awaited_once_with(hass, "entry-1", "agent-1")


@pytest.mark.asyncio
async def test_management_wrapper_routes_only_footprint_action(monkeypatch) -> None:
    original = AsyncMock(return_value={"source": "original"})
    wrapped = footprint._wrap_management_command(original)
    footprint_read = AsyncMock(return_value={"source": "footprint"})
    monkeypatch.setattr(footprint, "async_input_footprint", footprint_read)

    result = await wrapped(
        SimpleNamespace(),
        "user-1",
        True,
        {"section": "usage", "action": "footprint"},
    )
    assert result == {"source": "footprint"}
    footprint_read.assert_awaited_once()
    original.assert_not_awaited()

    result = await wrapped(
        SimpleNamespace(),
        "user-1",
        True,
        {"section": "usage", "action": "list"},
    )
    assert result == {"source": "original"}
    original.assert_awaited_once()


def test_install_input_footprint_is_idempotent(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    original_estimate = context_usage_hardening.estimate_provider_input_tokens

    async def original_command(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(footprint, "_INSTALLED", False)
    monkeypatch.setattr(management_ui, "async_management_command", original_command)

    footprint.install_input_footprint()
    wrapped_command = management_ui.async_management_command

    assert context_usage_hardening.estimate_provider_input_tokens is footprint._capture_live_footprint
    assert footprint._ORIGINAL_ESTIMATE is original_estimate
    assert wrapped_command is not original_command
    assert footprint._INSTALLED is True

    footprint.install_input_footprint()
    assert management_ui.async_management_command is wrapped_command
    assert context_usage_hardening.estimate_provider_input_tokens is footprint._capture_live_footprint


@pytest.mark.asyncio
async def test_input_footprint_usage_action_remains_admin_only() -> None:
    async def original(*_args, **_kwargs):
        return {"ok": True}

    wrapped = wrap_management_permissions(original)
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await wrapped(
            None,  # type: ignore[arg-type]
            "normal-user",
            False,
            {"section": "usage", "action": "footprint"},
        )
