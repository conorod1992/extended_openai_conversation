"""Focused mutation contracts for Guest Mode capability policy decisions."""

from custom_components.extended_openai_conversation_responses import guest_mode


def _policy(profile: str, **overrides: bool) -> guest_mode.GuestCapabilityPolicy:
    """Build a capability policy with a stable access-level value."""
    return guest_mode.GuestCapabilityPolicy(
        profile=profile,
        max_access_level=guest_mode.CONF_GUEST_MAX_ACCESS_LEVEL,
        **overrides,
    )


def test_custom_profile_denies_capabilities_without_explicit_opt_in() -> None:
    """Custom guest capabilities are opt-in rather than implicitly enabled."""
    policy = _policy(guest_mode.CONF_GUEST_CAPABILITY_PROFILE_CUSTOM)

    assert policy.allows_entity_reads() is False
    assert policy.allows_entity_writes() is False
    assert policy.allows_configured_tool() is False
    assert policy.allows_external_tool() is False


def test_custom_profile_honours_each_capability_flag_independently() -> None:
    """Each custom capability decision follows its own configured flag."""
    policy = _policy(
        guest_mode.CONF_GUEST_CAPABILITY_PROFILE_CUSTOM,
        allow_entity_reads=True,
        allow_entity_writes=False,
        allow_configured_tools=True,
        allow_external_tools=False,
    )

    assert policy.allows_entity_reads() is True
    assert policy.allows_entity_writes() is False
    assert policy.allows_configured_tool() is True
    assert policy.allows_external_tool() is False

    inverse = _policy(
        guest_mode.CONF_GUEST_CAPABILITY_PROFILE_CUSTOM,
        allow_entity_reads=False,
        allow_entity_writes=True,
        allow_configured_tools=False,
        allow_external_tools=True,
    )

    assert inverse.allows_entity_reads() is False
    assert inverse.allows_entity_writes() is True
    assert inverse.allows_configured_tool() is False
    assert inverse.allows_external_tool() is True


def test_non_custom_profile_never_enables_configured_or_external_tools() -> None:
    """Tool opt-in flags do not elevate a non-custom guest profile."""
    policy = _policy(
        guest_mode.CONF_GUEST_CAPABILITY_PROFILE_SAFE,
        allow_configured_tools=True,
        allow_external_tools=True,
    )

    assert policy.allows_configured_tool() is False
    assert policy.allows_external_tool() is False
