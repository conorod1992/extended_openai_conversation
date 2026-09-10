"""Focused mutation contracts for Guest Mode capability policy decisions."""

from custom_components.extended_openai_conversation_responses import guest_mode


def test_unrestricted_policy_allows_entities_and_configured_tools() -> None:
    """The normal non-guest policy leaves these capability sets unrestricted."""
    policy = guest_mode.GuestCapabilityPolicy.unrestricted()

    assert policy.guest_active is False
    assert policy.allows_entity_read("light.kitchen") is True
    assert policy.allows_entity_control("light.kitchen") is True
    assert policy.allows_configured_tool("weather_lookup") is True


def test_restricted_policy_enforces_entity_membership_independently() -> None:
    """Readable and controllable entity sets are independent guest boundaries."""
    policy = guest_mode.GuestCapabilityPolicy(
        guest_active=True,
        readable_entity_ids=frozenset({"sensor.temperature", "light.kitchen"}),
        controllable_entity_ids=frozenset({"light.kitchen"}),
    )

    assert policy.allows_entity_read("sensor.temperature") is True
    assert policy.allows_entity_read("light.kitchen") is True
    assert policy.allows_entity_read("lock.front_door") is False

    assert policy.allows_entity_control("light.kitchen") is True
    assert policy.allows_entity_control("sensor.temperature") is False
    assert policy.allows_entity_control("lock.front_door") is False


def test_empty_capability_sets_fail_closed() -> None:
    """An explicit empty guest capability set denies every candidate."""
    policy = guest_mode.GuestCapabilityPolicy(
        guest_active=True,
        readable_entity_ids=frozenset(),
        controllable_entity_ids=frozenset(),
        configured_tool_names=frozenset(),
    )

    assert policy.allows_entity_read("light.kitchen") is False
    assert policy.allows_entity_control("light.kitchen") is False
    assert policy.allows_configured_tool("weather_lookup") is False


def test_configured_tool_policy_uses_exact_membership() -> None:
    """Configured tools are allowed only when present in the resolved guest set."""
    policy = guest_mode.GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset({"weather_lookup", "calendar_read"}),
    )

    assert policy.allows_configured_tool("weather_lookup") is True
    assert policy.allows_configured_tool("calendar_read") is True
    assert policy.allows_configured_tool("calendar_write") is False
