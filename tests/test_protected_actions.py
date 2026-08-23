"""Protected Action policy, local challenge, and PIN privacy tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    protected_actions_allowed_by_guest,
    redact_pin_reply,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from custom_components.extended_openai_conversation_responses.protected_actions import (
    MAX_PIN_ATTEMPTS,
    ProtectedActionRequired,
    ProtectedActions,
    ProtectionContext,
    action_matches_rule,
    normalize_spoken_pin,
    reset_active_protection,
    set_active_protection,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    async_evaluate_rule,
)
from homeassistant.components import conversation

from .test_request_rules import FakeServices, local_rule, manager as request_manager


class MemoryStore:
    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


async def protected_manager(rule=None, pin=None) -> ProtectedActions:
    result = ProtectedActions(MemoryStore())
    await result.async_initialize()
    if pin:
        await result.async_set_pin(pin)
    if rule:
        await result.async_create(rule)
    return result


def rule(protection="confirmation", **updates):
    value = {
        "name": "Front door unlock",
        "domain": "lock",
        "service": "unlock",
        "protection": protection,
        "enabled": True,
    }
    value.update(updates)
    return value


def context(
    conversation_id="conversation-one",
    user_id="owner",
    device_id="device",
    satellite_id="satellite",
):
    return ProtectionContext(
        "entry", "agent", conversation_id, user_id, device_id, satellite_id
    )


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("1234", "1234"),
        ("1 2 3 4", "1234"),
        ("one two three four", "1234"),
        ("one too three for", "1234"),
        ("ONE TWO THREE FOUR", "1234"),
        ("one, two, three, four", "1234"),
        ("one won too ate", "1128"),
        ("twelve thirty-four", None),
        ("one tree three four", None),
        ("123 4", None),
        ("one two maybe four", None),
    ],
)
def test_spoken_pin_normalization_is_deterministic(spoken, expected) -> None:
    assert normalize_spoken_pin(spoken) == expected


async def test_unprotected_action_executes_normally() -> None:
    manager = await protected_manager()
    services = FakeServices()
    hass = SimpleNamespace(services=services)
    token = set_active_protection(manager, context())
    try:
        await async_call_ha_action(
            hass, "light", "turn_on", target={"entity_id": "light.kitchen"}
        )
    finally:
        reset_active_protection(token)
    assert len(services.calls) == 1


async def test_confirmation_required_rejected_and_accepted_locally() -> None:
    manager = await protected_manager(rule())
    services = FakeServices()
    hass = SimpleNamespace(services=services)
    identity = context()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired, match="Are you sure"):
            await async_call_ha_action(
                hass, "lock", "unlock", data={"entity_id": "lock.front_door"}
            )
    finally:
        reset_active_protection(token)
    assert services.calls == []
    reminder = await manager.async_handle_reply(identity, "maybe later")
    assert reminder.handled and not reminder.actions
    cancelled = await manager.async_handle_reply(identity, "no")
    assert cancelled.response == "Cancelled."
    assert not (await manager.async_handle_reply(identity, "yes")).handled

    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(hass, "lock", "unlock")
    finally:
        reset_active_protection(token)
    accepted = await manager.async_handle_reply(identity, "go ahead")
    assert accepted.response == "Done."
    assert accepted.actions[0]["service"] == "unlock"


async def test_confirmation_cannot_authorize_different_identity() -> None:
    manager = await protected_manager(rule())
    token = set_active_protection(manager, context())
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    assert not (
        await manager.async_handle_reply(context(user_id="someone-else"), "yes")
    ).handled
    assert not (
        await manager.async_handle_reply(context(conversation_id="other"), "yes")
    ).handled
    assert not (
        await manager.async_handle_reply(context(satellite_id="other-satellite"), "yes")
    ).handled


async def test_confirmation_expires(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.protected_actions.monotonic",
        lambda: now,
    )
    manager = await protected_manager(rule())
    identity = context()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    now += 121
    expired = await manager.async_handle_reply(identity, "yes")
    assert expired.handled and "expired" in expired.response


async def test_reload_cancels_and_multiple_identities_stay_separate() -> None:
    manager = await protected_manager(rule())
    first = context(user_id="first")
    second = context(user_id="second")
    for identity in (first, second):
        token = set_active_protection(manager, identity)
        try:
            with pytest.raises(ProtectedActionRequired):
                await async_call_ha_action(
                    SimpleNamespace(services=FakeServices()), "lock", "unlock"
                )
        finally:
            reset_active_protection(token)
    assert (await manager.async_handle_reply(first, "yes")).actions
    assert (await manager.async_handle_reply(second, "yes")).actions

    token = set_active_protection(manager, first)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    manager.cancel_pending()
    assert not (await manager.async_handle_reply(first, "yes")).handled


async def test_pin_hash_privacy_correct_pin_and_no_fuzzy_matching() -> None:
    manager = await protected_manager(rule("pin"), pin="1234")
    backup = await manager.async_backup_data()
    assert backup["pin_hash"] != "1234"
    assert backup["pin_hash"].startswith("pbkdf2_sha256$")
    assert "pin_hash" not in manager.snapshot()
    identity = context()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired, match="say your PIN"):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    invalid = await manager.async_handle_reply(identity, "one tree three four")
    assert invalid.response == "Please say your PIN one digit at a time."
    assert invalid.redact_input
    accepted = await manager.async_handle_reply(identity, "one too three for")
    assert accepted.actions and accepted.response == "Done." and accepted.redact_input


async def test_pin_failure_limit_and_cooldown() -> None:
    manager = await protected_manager(rule("pin"), pin="1234")
    identity = context()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    for attempt in range(MAX_PIN_ATTEMPTS):
        result = await manager.async_handle_reply(identity, "9 9 9 9")
        assert not result.actions
        if attempt < MAX_PIN_ATTEMPTS - 1:
            assert result.response == "That PIN was not accepted."
    assert "wait" in result.response.lower()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired, match="wait"):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)


async def test_pin_values_are_not_logged(caplog) -> None:
    manager = await protected_manager(rule("pin"), pin="2468")
    identity = context()
    token = set_active_protection(manager, identity)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_call_ha_action(
                SimpleNamespace(services=FakeServices()), "lock", "unlock"
            )
    finally:
        reset_active_protection(token)
    await manager.async_handle_reply(identity, "nine eight seven six")
    log_text = caplog.text.casefold()
    assert "2468" not in log_text
    assert "nine eight seven six" not in log_text


async def test_pin_rule_requires_configured_pin() -> None:
    manager = await protected_manager()
    with pytest.raises(ValueError, match="Set a PIN"):
        await manager.async_create(rule("pin"))


def test_optional_target_matching() -> None:
    target_rule = {
        **rule(),
        "id": "rule",
        "order": 0,
        "entity_id": ["lock.front_door"],
        "device_id": [],
        "area_id": [],
    }
    assert action_matches_rule(
        {
            "domain": "lock",
            "service": "unlock",
            "data": {"entity_id": "lock.front_door"},
        },
        target_rule,
    )
    assert not action_matches_rule(
        {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.back_door"},
        },
        target_rule,
    )


async def test_local_request_rule_uses_same_protection_seam() -> None:
    protected = await protected_manager(
        rule("confirmation", domain="script", service="turn_on")
    )
    rules = await request_manager(local_rule())
    services = FakeServices()
    identity = context()
    token = set_active_protection(protected, identity)
    try:
        with pytest.raises(ProtectedActionRequired):
            await async_evaluate_rule(
                SimpleNamespace(services=services),
                rules,
                RequestRuleRuntime(),
                "good night",
                "conversation:one",
            )
    finally:
        reset_active_protection(token)
    assert services.calls == []


def test_correct_pin_cannot_bypass_guest_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        lambda _hass, _selection: SimpleNamespace(
            referenced={"lock.front_door"}, indirectly_referenced=set()
        ),
    )
    actions = (
        {
            "domain": "lock",
            "service": "unlock",
            "data": {},
            "target": {"entity_id": "lock.front_door"},
        },
    )
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset(),
        controllable_entity_ids=frozenset(),
    )
    assert not protected_actions_allowed_by_guest(SimpleNamespace(), actions, policy)


def test_pin_reply_is_removed_from_future_model_history() -> None:
    user_input = SimpleNamespace(text="one too three for")
    chat_log = SimpleNamespace(
        content=[conversation.UserContent(content="one too three for")]
    )
    redact_pin_reply(user_input, chat_log)
    assert "one too three for" not in user_input.text
    assert "one too three for" not in chat_log.content[-1].content
