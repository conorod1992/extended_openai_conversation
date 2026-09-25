"""Request Rules runtime, local-action, guest-mode, and security regressions."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
    GuestModeDenied,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    _reset_request_rule_runtime,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RuleMatch,
    _guest_script_allowed,
    _resolve_guest_slot_templates,
    async_evaluate_rule,
    get_request_rule_runtime,
    rule_has_sensitive_actions,
)


def test_nested_sensitive_action_is_detected() -> None:
    rule = {
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "choose": [
                        {
                            "conditions": [],
                            "sequence": [
                                {
                                    "action": "lock.unlock",
                                    "target": {"entity_id": "lock.front_door"},
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    }
    assert rule_has_sensitive_actions(rule)


def test_guest_preflight_checks_nested_configured_functions() -> None:
    actions = [
        {"delay": "00:00:01"},
        {
            "choose": [
                {
                    "conditions": [],
                    "sequence": [
                        {
                            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                            "data": {"function": "owner_only", "arguments": {}},
                        }
                    ],
                }
            ]
        },
    ]
    policy = GuestCapabilityPolicy(True, configured_tool_names=frozenset())
    assert not _guest_script_allowed(SimpleNamespace(), actions, policy)


def test_guest_slot_templates_allow_only_deterministic_captures() -> None:
    assert _resolve_guest_slot_templates(
        {
            "data": {
                "direct": "{{ item }}",
                "qualified": "{{ request.slots.item }}",
            }
        },
        {"item": "milk"},
    ) == {"data": {"direct": "milk", "qualified": "milk"}}

    with pytest.raises(GuestModeDenied):
        _resolve_guest_slot_templates("{{ request.slots.missing }}", {"item": "milk"})
    with pytest.raises(GuestModeDenied):
        _resolve_guest_slot_templates("{{ item | upper }}", {"item": "milk"})


@pytest.mark.asyncio
async def test_guest_denial_happens_before_script_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    class FailIfConstructed:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal constructed
            constructed = True

    import custom_components.extended_openai_conversation_responses.request_rules as module

    monkeypatch.setattr(module, "Script", FailIfConstructed)
    rule = {
        "id": "nested-guest",
        "name": "Nested guest action",
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {"delay": "00:00:01"},
                {
                    "choose": [
                        {
                            "conditions": [],
                            "sequence": [
                                {
                                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                                    "data": {
                                        "function": "owner_only",
                                        "arguments": {},
                                    },
                                }
                            ],
                        }
                    ]
                },
            ],
            "success_response": "Done",
            "failure_response": "Failed",
        },
    }

    class Rules:
        def match(self, _text: str) -> RuleMatch:
            return RuleMatch(rule, "run it", False, 100.0)

        async def async_match(self, _hass: Any, text: str) -> RuleMatch:
            return self.match(text)

    evaluation = await async_evaluate_rule(
        SimpleNamespace(),
        Rules(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        "run it",
        "continuity:test",
        guest_policy=GuestCapabilityPolicy(True, configured_tool_names=frozenset()),
    )
    assert evaluation is not None
    assert evaluation.response == GUEST_MODE_UNAVAILABLE
    assert evaluation.successful is False
    assert constructed is False


@pytest.mark.asyncio
async def test_failed_local_rule_is_archived_and_recorded_as_failed() -> None:
    class Usage:
        def __init__(self) -> None:
            self.run = SimpleNamespace(run_id="run-1", successful=True, error_type=None)

        @asynccontextmanager
        async def async_run(self, **_kwargs: Any):
            yield self.run

        def mark_current_run_failed(self, error_type: str) -> None:
            self.run.successful = False
            self.run.error_type = error_type

    class Continuity:
        def __init__(self) -> None:
            self.successes: list[str | None] = []
            self.releases: list[str | None] = []

        async def async_record_success(
            self, key: str | None, _claim_token: str | None, _content: Any
        ) -> None:
            self.successes.append(key)

        async def async_release(
            self, key: str | None, _claim_token: str | None
        ) -> None:
            self.releases.append(key)

    class Agent:
        def __init__(self) -> None:
            self._usage = Usage()
            self._continuity = Continuity()
            self.archived: list[bool] = []

        def _local_rule_result(self, *_args: Any, successful: bool) -> str:
            assert successful is False
            return "result"

        async def _async_archive_turn(self, *_args: Any, successful: bool) -> None:
            self.archived.append(successful)

    agent = Agent()
    result = await ExtendedOpenAIAgentEntity._async_complete_local_rule(
        agent,  # type: ignore[arg-type]
        SimpleNamespace(conversation_id="conversation-1"),
        SimpleNamespace(content=["user", "assistant"]),
        "Failed",
        None,
        "owner-key",
        "claim-token",
        None,
        successful=False,
    )

    assert result == "result"
    assert agent.archived == [False]
    assert agent._usage.run.successful is False
    assert agent._usage.run.error_type == "RequestRuleExecutionFailed"
    assert agent._continuity.successes == []
    # Release is owned by the outer request-finalization boundary, not this helper.
    assert agent._continuity.releases == []


def test_ending_continuity_clears_request_rule_routing_state() -> None:
    hass = SimpleNamespace(data={})
    runtime = get_request_rule_runtime(hass, "entry", "agent")
    runtime.set("continuity:owner-key", {"chat_model": "gpt-test"})
    assert runtime.get("continuity:owner-key")

    _reset_request_rule_runtime(hass, "entry", "agent", "owner-key")
    assert runtime.get("continuity:owner-key") == {}


# Runtime and authorization boundary coverage consolidated from the residual layer.

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from homeassistant.exceptions import HomeAssistantError


def test_total_pattern_state_validation_handles_inactive_and_mismatched_rules() -> None:
    """Inactive broken patterns are repairable while active broken patterns fail."""
    invalid = {
        "id": "broken",
        "name": "Broken",
        "enabled": True,
        "order": 0,
        "match_type": "sentence_pattern",
        "phrases": ["first", "second"],
    }
    first = SimpleNamespace(capture_names={"room"}, state_count=1)
    second = SimpleNamespace(capture_names={"device"}, state_count=1)

    with patch.object(rr, "compile_sentence_pattern", side_effect=[first, second]):
        rr._validate_total_pattern_states([invalid], inactive_rule_ids={"broken"})

    with (
        patch.object(rr, "compile_sentence_pattern", side_effect=[first, second]),
        pytest.raises(ValueError, match="same slots"),
    ):
        rr._validate_total_pattern_states([invalid])

    # Disabled and malformed phrase containers are deliberately ignored here;
    # rule-level validation owns their shape errors.
    rr._validate_total_pattern_states(
        [
            {**invalid, "id": "disabled", "enabled": False},
            {**invalid, "id": "not-pattern", "match_type": "equals"},
            {**invalid, "id": "bad-phrases", "phrases": "not-a-sequence"},
        ]
    )


def test_legacy_action_migration_covers_unknown_source_and_home_assistant_type() -> None:
    """Legacy actions reject unknown bindings and strip the HA type wrapper."""
    with pytest.raises(ValueError, match="unknown function action fields"):
        rr._validate_local_action(
            {
                "type": "function",
                "function": "demo",
                "arguments": {},
                "unexpected": True,
            }
        )

    with pytest.raises(ValueError, match="source must be fixed or slot"):
        rr._validate_local_action(
            {
                "type": "function",
                "function": "demo",
                "arguments": {"value": {"source": "dynamic", "value": 1}},
            }
        )

    assert rr._validate_local_action(
        {
            "type": "home_assistant",
            "domain": "light",
            "service": "turn_on",
            "target": {"entity_id": "light.kitchen"},
            "data": {},
        }
    ) == {
        "action": "light.turn_on",
        "target": {"entity_id": "light.kitchen"},
        "data": {},
    }


def test_legacy_slot_migration_recurses_and_slot_discovery_ignores_native_actions() -> None:
    """Legacy slot syntax is migrated recursively without misclassifying native actions."""
    assert rr._migrate_slot_templates(
        {
            "text": "Weather in {place}",
            "nested": [{"value_from": "slot", "slot": "room"}],
        }
    ) == {
        "text": "Weather in {{ place }}",
        "nested": ["{{ room }}"],
    }

    assert rr._legacy_action_slots(
        {
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
                {
                    "type": "function",
                    "function": "weather",
                    "arguments": {"place": {"source": "slot", "slot": "place"}},
                },
                "not-an-action",
            ]
        }
    ) == {"place"}
    assert rr._legacy_action_slots({"actions": "not-a-sequence"}) == set()


def test_script_template_masking_preserves_shape_and_masks_action_templates() -> None:
    """Schema-only masking replaces templates without mutating their container shape."""
    value = {
        "action": "{{ request.slots.action }}",
        "data": {
            "message": "Hello {{ request.slots.name }}",
            "items": ["plain", "{% if true %}templated{% endif %}"],
        },
    }

    masked = rr._mask_script_templates(value)

    assert masked == {
        "action": "homeassistant.update_entity",
        "data": {
            "message": "request_rule_template",
            "items": ["plain", "request_rule_template"],
        },
    }
    # The stored input remains untouched; masking is only for context-free schema checks.
    assert value["action"] == "{{ request.slots.action }}"


def test_script_iterator_skips_malformed_nested_shapes_and_bounds_depth() -> None:
    """Malformed optional branches are ignored while excessive valid nesting is rejected."""
    root = {
        "action": "light.turn_on",
        "sequence": "not-a-sequence",
        "choose": ["bad-branch", {"sequence": "bad-sequence"}],
        "repeat": {"sequence": "bad-sequence"},
    }
    assert list(rr._iter_script_actions([root])) == [root]

    nested = [{"action": "light.turn_on"}]
    for _ in range(rr.MAX_SCRIPT_DEPTH + 1):
        nested = [{"sequence": nested}]
    with pytest.raises(ValueError, match="maximum depth"):
        list(rr._iter_script_actions(nested))


def test_sensitive_action_detection_handles_nonlocal_and_cover_controls() -> None:
    """Sensitivity detection distinguishes routing rules from security-relevant cover control."""
    assert not rr.rule_has_sensitive_actions(
        {"action_type": "route_to_ai", "action": {"actions": [{"action": "lock.unlock"}]}}
    )
    assert rr.rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.open_cover"}]},
        }
    )
    assert not rr.rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.stop_cover"}]},
        }
    )


def test_guest_preflight_accepts_allowed_configured_tool() -> None:
    """The positive configured-tool path is authorized without HA service dispatch."""
    policy = SimpleNamespace(allows_configured_tool=lambda name: name == "weather")
    assert rr._guest_script_allowed(
        object(),
        [
            {
                "action": f"{rr.DOMAIN}.{rr.SERVICE_CALL_FUNCTION}",
                "data": {"function": "weather", "arguments": {}},
            }
        ],
        policy,
    )


def test_guest_preflight_rejects_missing_or_disallowed_configured_tool() -> None:
    """Guest preflight rejects configured-function actions unless the named tool is allowed."""
    policy = rr.GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset({"safe_tool"}),
    )
    service = f"{rr.DOMAIN}.{rr.SERVICE_CALL_FUNCTION}"

    assert not rr._guest_script_allowed(
        SimpleNamespace(),
        [{"action": service, "data": {"function": 42}}],
        policy,
    )
    assert not rr._guest_script_allowed(
        SimpleNamespace(),
        [{"action": service, "data": {"function": "blocked_tool"}}],
        policy,
    )


def test_invalid_native_script_sequence_is_reported_as_value_error() -> None:
    """Home Assistant schema failures are normalized at the Request Rules boundary."""
    with pytest.raises(ValueError, match="invalid Home Assistant action sequence"):
        rr._validate_script_sequence([{"action": 12345}])


@pytest.mark.asyncio
async def test_local_action_failure_unloads_script_and_clears_active_executor() -> None:
    """A failing HA script cannot leak its function executor into later requests."""
    rule = {
        "id": "runtime-cleanup",
        "name": "Kitchen lights",
        "action_type": "local_action",
        "action": {
            "actions": [{"action": "light.turn_on"}],
            "success_response": "Done",
            "failure_response": "Failed {room}",
        },
    }
    match = rr.RuleMatch(rule, "run", False, 1.0, {"room": "kitchen"})
    rules = SimpleNamespace(async_match=AsyncMock(return_value=match))
    runtime = rr.RequestRuleRuntime()
    executor = AsyncMock(return_value={"ok": True})

    script = Mock()
    script.async_run = AsyncMock(side_effect=RuntimeError("boom"))
    script.async_unload = AsyncMock()

    with (
        patch.object(rr.cv, "SCRIPT_SCHEMA", side_effect=lambda actions: actions),
        patch.object(
            rr,
            "async_validate_actions_config",
            new=AsyncMock(side_effect=lambda _hass, actions: actions),
        ),
        patch.object(rr, "Script", return_value=script),
        patch.object(rr, "_LOGGER") as logger,
    ):
        result = await rr.async_evaluate_rule(
            Mock(),
            rules,
            runtime,
            "run",
            "session",
            function_executor=executor,
        )

    assert result is not None
    assert result.successful is False
    assert result.response == "Failed kitchen"
    script.async_run.assert_awaited_once()
    script.async_unload.assert_awaited_once_with()
    logger.exception.assert_called_once()
    log_args = logger.exception.call_args.args
    assert "Extended OpenAI > Request Rules" in log_args[0]
    assert log_args[1] == "Kitchen lights"

    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("should-not-leak", {})


@pytest.mark.asyncio
async def test_guest_authorization_exception_fails_closed_before_script_creation() -> None:
    """Unexpected Guest preflight errors deny execution rather than bypassing policy."""
    rule = {
        "id": "guest-preflight",
        "action_type": "local_action",
        "action": {
            "actions": [{"action": "light.turn_on"}],
            "success_response": "Done",
            "failure_response": "Failed",
        },
    }
    match = rr.RuleMatch(rule, "run", False, 1.0, {})
    rules = SimpleNamespace(async_match=AsyncMock(return_value=match))
    policy = SimpleNamespace(guest_active=True)

    with (
        patch.object(rr, "_resolve_guest_slot_templates", side_effect=RuntimeError("bad")),
        patch.object(rr, "Script") as script_cls,
    ):
        result = await rr.async_evaluate_rule(
            Mock(),
            rules,
            rr.RequestRuleRuntime(),
            "run",
            "session",
            guest_policy=policy,
        )

    assert result is not None
    assert result.successful is False
    assert result.response == rr.GUEST_MODE_UNAVAILABLE
    script_cls.assert_not_called()
