"""Focused mutation contracts for basic Home Assistant permission enforcement."""

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import ha_permissions


class _Permissions:
    """Minimal permission object for direct entity CONTROL checks."""

    def __init__(self, controllable: set[str], *, access_all: bool = False) -> None:
        self._controllable = controllable
        self._access_all = access_all

    def check_entity(self, entity_id: str, policy: str) -> bool:
        assert policy == ha_permissions.POLICY_CONTROL
        return entity_id in self._controllable

    def access_all_entities(self, policy: str) -> bool:
        assert policy == ha_permissions.POLICY_CONTROL
        return self._access_all


class _Auth:
    """Minimal async Home Assistant auth lookup."""

    def __init__(self, users: dict[str, object]) -> None:
        self._users = users

    async def async_get_user(self, user_id: str) -> object | None:
        return self._users.get(user_id)


def _cached_hass(user_id: str, user: object) -> SimpleNamespace:
    return SimpleNamespace(data={ha_permissions._USER_CACHE_KEY: {user_id: user}})


def test_entity_filter_preserves_existing_exposure_without_authenticated_user() -> None:
    """System/voice calls without a HA user keep the pre-existing exposure boundary."""
    entities = [
        {"entity_id": "light.kitchen", "name": "Kitchen"},
        {"entity_id": "switch.fan", "name": "Fan"},
    ]
    hass = SimpleNamespace(data={})

    with ha_permissions.bind_active_ha_context(None):
        result = ha_permissions.filter_entities_for_active_user(hass, entities)

    assert result is entities


def test_entity_filter_intersects_authenticated_user_read_permissions(monkeypatch) -> None:
    """A basic authenticated route exposes only entities allowed by HA READ policy."""
    user_id = "user-1"
    user = SimpleNamespace(is_active=True)
    hass = _cached_hass(user_id, user)
    context = SimpleNamespace(user_id=user_id)
    entities = [
        {"entity_id": "light.allowed"},
        {"entity_id": "light.denied"},
        {"name": "missing entity id"},
    ]

    def _filter(candidate_user, entity_ids, policy):
        assert candidate_user is user
        assert entity_ids == ["light.allowed", "light.denied"]
        assert policy == ha_permissions.POLICY_READ
        return ["light.allowed"]

    monkeypatch.setattr(ha_permissions, "filter_entity_ids_by_permission", _filter)

    with ha_permissions.bind_active_ha_context(context):
        result = ha_permissions.filter_entities_for_active_user(hass, entities)

    assert result == [{"entity_id": "light.allowed"}]


@pytest.mark.parametrize("cached_user", [None, SimpleNamespace(is_active=False)])
def test_entity_filter_fails_closed_for_unavailable_authenticated_user(cached_user) -> None:
    """A known authenticated identity is not exposed while its user is unavailable."""
    user_id = "user-1"
    cache = {} if cached_user is None else {user_id: cached_user}
    hass = SimpleNamespace(data={ha_permissions._USER_CACHE_KEY: cache})
    context = SimpleNamespace(user_id=user_id)
    entities = [{"entity_id": "light.kitchen"}]

    with ha_permissions.bind_active_ha_context(context):
        result = ha_permissions.filter_entities_for_active_user(hass, entities)

    assert result == []


def test_entity_filter_fails_closed_before_permission_cache_is_initialized() -> None:
    """An authenticated request sees nothing if the permission cache is unavailable."""
    context = SimpleNamespace(user_id="user-1")
    entities = [{"entity_id": "light.kitchen"}]
    hass = SimpleNamespace(data={})

    with ha_permissions.bind_active_ha_context(context):
        result = ha_permissions.filter_entities_for_active_user(hass, entities)

    assert result == []


@pytest.mark.asyncio
async def test_direct_control_permission_allows_permitted_entities() -> None:
    """The simple resolved-target route accepts entities with HA CONTROL permission."""
    context = SimpleNamespace(user_id="user-1")
    user = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=_Permissions({"light.kitchen", "switch.fan"}),
    )
    hass = SimpleNamespace(auth=_Auth({"user-1": user}))

    result = await ha_permissions.async_require_control_permission(
        hass,
        ["light.kitchen", "switch.fan"],
        context=context,
    )

    assert result is context


@pytest.mark.asyncio
async def test_direct_control_permission_rejects_any_denied_entity() -> None:
    """The simple resolved-target route fails when any entity lacks CONTROL permission."""
    context = SimpleNamespace(user_id="user-1")
    user = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=_Permissions({"light.allowed"}),
    )
    hass = SimpleNamespace(auth=_Auth({"user-1": user}))

    with pytest.raises(HomeAssistantError, match="light.denied"):
        await ha_permissions.async_require_control_permission(
            hass,
            ["light.allowed", "light.denied"],
            context=context,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, SimpleNamespace(is_active=False)])
async def test_direct_control_permission_rejects_unavailable_or_inactive_user(user) -> None:
    """An authenticated direct-control request fails closed without an active HA user."""
    context = SimpleNamespace(user_id="user-1")
    users = {} if user is None else {"user-1": user}
    hass = SimpleNamespace(auth=_Auth(users))

    with pytest.raises(HomeAssistantError):
        await ha_permissions.async_require_control_permission(
            hass,
            ["light.kitchen"],
            context=context,
        )


@pytest.mark.asyncio
async def test_empty_direct_target_is_allowed_for_admin_user() -> None:
    """Admins may authorize a direct action whose entity target cannot be resolved."""
    context = SimpleNamespace(user_id="user-1")
    user = SimpleNamespace(
        is_active=True,
        is_admin=True,
        permissions=_Permissions(set(), access_all=False),
    )
    hass = SimpleNamespace(auth=_Auth({"user-1": user}))

    result = await ha_permissions.async_require_control_permission(
        hass,
        [],
        context=context,
    )

    assert result is context


@pytest.mark.asyncio
async def test_empty_direct_target_is_allowed_with_global_control_permission() -> None:
    """A non-admin with global CONTROL permission may authorize an unresolved target."""
    context = SimpleNamespace(user_id="user-1")
    user = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=_Permissions(set(), access_all=True),
    )
    hass = SimpleNamespace(auth=_Auth({"user-1": user}))

    result = await ha_permissions.async_require_control_permission(
        hass,
        [],
        context=context,
    )

    assert result is context


@pytest.mark.asyncio
async def test_empty_direct_target_is_rejected_without_global_control_permission() -> None:
    """A normal user cannot authorize a direct action with no resolvable entity target."""
    context = SimpleNamespace(user_id="user-1")
    user = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=_Permissions(set(), access_all=False),
    )
    hass = SimpleNamespace(auth=_Auth({"user-1": user}))

    with pytest.raises(HomeAssistantError):
        await ha_permissions.async_require_control_permission(
            hass,
            [],
            context=context,
        )
