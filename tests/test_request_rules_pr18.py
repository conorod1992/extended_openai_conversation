"""PR18 Request Rule execution-safety regressions."""

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

        def _local_rule_result(self, *_args: Any) -> str:
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
