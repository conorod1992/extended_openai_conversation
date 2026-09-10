"""Focused mutation contracts for Guest Mode capability policy decisions."""

from types import SimpleNamespace

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


def test_resolve_guest_policy_is_unrestricted_without_loaded_manager(monkeypatch) -> None:
    """No loaded Guest Mode manager means the normal unrestricted policy."""
    monkeypatch.setattr(
        guest_mode,
        "_resolve_exclusion_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected v2 resolver")),
    )
    monkeypatch.setattr(
        guest_mode,
        "_resolve_legacy_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected legacy resolver")),
    )

    policy = guest_mode.resolve_guest_policy(SimpleNamespace(), {}, None)

    assert policy.guest_active is False


def test_resolve_guest_policy_is_unrestricted_when_manager_inactive(monkeypatch) -> None:
    """An inactive Guest Mode manager must not apply guest restrictions."""
    manager = SimpleNamespace(is_active=lambda: False)
    monkeypatch.setattr(
        guest_mode,
        "_resolve_exclusion_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected v2 resolver")),
    )
    monkeypatch.setattr(
        guest_mode,
        "_resolve_legacy_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected legacy resolver")),
    )

    policy = guest_mode.resolve_guest_policy(SimpleNamespace(), {}, manager)

    assert policy.guest_active is False


def test_resolve_guest_policy_routes_current_policy_to_exclusion_resolver(monkeypatch) -> None:
    """An active current-version Guest policy uses the v2 exclusion resolver."""
    manager = SimpleNamespace(is_active=lambda: True)
    expected = guest_mode.GuestCapabilityPolicy(True, readable_entity_ids=frozenset())
    called: list[tuple[object, object, object]] = []

    def _resolve(hass, options, configured_tools):
        called.append((hass, options, configured_tools))
        return expected

    monkeypatch.setattr(guest_mode, "_resolve_exclusion_policy", _resolve)
    monkeypatch.setattr(
        guest_mode,
        "_resolve_legacy_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected legacy resolver")),
    )
    hass = SimpleNamespace()
    options = {guest_mode.CONF_GUEST_POLICY_VERSION: guest_mode.GUEST_POLICY_VERSION}
    configured_tools = ({"spec": {"name": "weather_lookup"}},)

    result = guest_mode.resolve_guest_policy(hass, options, manager, configured_tools)

    assert result is expected
    assert called == [(hass, options, configured_tools)]


def test_resolve_guest_policy_routes_legacy_policy_to_legacy_resolver(monkeypatch) -> None:
    """An active policy without the current version stays on the legacy resolver."""
    manager = SimpleNamespace(is_active=lambda: True)
    expected = guest_mode.GuestCapabilityPolicy(True, readable_entity_ids=frozenset())
    called: list[tuple[object, object, object]] = []

    def _resolve(hass, options, configured_tools):
        called.append((hass, options, configured_tools))
        return expected

    monkeypatch.setattr(guest_mode, "_resolve_legacy_policy", _resolve)
    monkeypatch.setattr(
        guest_mode,
        "_resolve_exclusion_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected v2 resolver")),
    )
    hass = SimpleNamespace()
    options: dict[str, object] = {}
    configured_tools = ({"spec": {"name": "weather_lookup"}},)

    result = guest_mode.resolve_guest_policy(hass, options, manager, configured_tools)

    assert result is expected
    assert called == [(hass, options, configured_tools)]
