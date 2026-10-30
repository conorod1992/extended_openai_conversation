"""Queue admission owns permission, current eligibility and atomic insertion."""

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

KITCHEN = "assist_satellite.kitchen"
HALL = "assist_satellite.hall"


@pytest.fixture
def admission(hass, monkeypatch):
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "_refresh_state_listener", lambda: None)
    monkeypatch.setattr(manager, "_schedule_drain", lambda _: None)
    monkeypatch.setattr(
        intercom.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None)
    )
    states = [
        SimpleNamespace(
            entity_id=KITCHEN,
            attributes={"supported_features": intercom.ANNOUNCE_FEATURE},
        )
    ]
    hass.states.async_all.side_effect = lambda _: list(states)
    hass.states.get.return_value = SimpleNamespace(state="responding")
    monkeypatch.setattr(
        intercom, "async_call_later", MagicMock(return_value=lambda: None)
    )
    check = MagicMock(return_value=True)
    user = SimpleNamespace(
        is_active=True, permissions=SimpleNamespace(check_entity=check)
    )
    hass.auth.async_get_user = AsyncMock(return_value=user)
    return manager, states, user, check


def assert_no_admission(manager):
    assert manager.history() == []
    assert manager._queues == {}
    assert manager._expiry_by_message == {}


async def test_direct_send_cannot_bypass_control_permission(hass, admission):
    manager, _, _, check = admission
    check.return_value = False
    rejected = False
    try:
        await manager.async_send(
            "Dinner", whole_home=True, context=Context(user_id="restricted")
        )
    except HomeAssistantError:
        rejected = True
    assert rejected, "public queue admission must authorize the concrete recipients"
    assert_no_admission(manager)
    # The next permitted action creates exactly one admission, without stale state.
    check.return_value = True
    result = await manager.async_send(
        "Allowed", whole_home=True, context=Context(user_id="restricted")
    )
    assert [item["id"] for item in manager.history()] == [result["id"]]
    assert len(manager._queues[KITCHEN]) == 1
    hass.auth.async_get_user.assert_awaited_with("restricted")


@pytest.mark.parametrize(
    "change", ["disabled", "closed", "capacity", "disappeared", "cancelled", "joined"]
)
async def test_permission_await_rechecks_admission_without_expanding_targets(
    hass, admission, change, monkeypatch
):
    manager, states, user, check = admission
    entered, release = asyncio.Event(), asyncio.Event()

    async def lookup(_):
        entered.set()
        await release.wait()
        return user

    hass.auth.async_get_user.side_effect = lookup
    send = asyncio.create_task(
        manager.async_send("Dinner", whole_home=True, context=Context(user_id="user"))
    )
    await asyncio.wait_for(entered.wait(), 1)
    if change == "disabled":
        manager._enabled = False
    elif change == "closed":
        manager._closed = True
    elif change == "capacity":
        monkeypatch.setattr(intercom, "MAX_BROADCAST_MESSAGES", 0)
    elif change == "disappeared":
        states.clear()
    elif change == "joined":
        states.append(
            SimpleNamespace(
                entity_id=HALL,
                attributes={"supported_features": intercom.ANNOUNCE_FEATURE},
            )
        )
    else:
        send.cancel()
    release.set()
    if change == "joined":
        result = await send
        assert result["targets"] == [KITCHEN]
        assert set(manager._queues) == {KITCHEN}
        assert len(manager.history()) == 1
    else:
        with pytest.raises(
            asyncio.CancelledError if change == "cancelled" else HomeAssistantError
        ):
            await send
        assert_no_admission(manager)
    check.assert_not_called() if change == "cancelled" else check.assert_called_once_with(
        KITCHEN, "control"
    )


async def test_authorization_oracle_detects_removed_manager_boundary(
    hass, admission, monkeypatch
):
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(intercom.IntercomManager.async_send))
    )
    function = tree.body[0]
    assignment = next(
        node
        for node in function.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)
    )
    assignment.value = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="self", ctx=ast.Load()),
            attr="resolve_targets",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[
            ast.keyword(
                arg="whole_home", value=ast.Name(id="whole_home", ctx=ast.Load())
            )
        ],
    )
    namespace = dict(vars(intercom))
    exec(
        compile(
            ast.fix_missing_locations(tree), "<removed-manager-authorization>", "exec"
        ),
        namespace,
    )
    monkeypatch.setattr(intercom.IntercomManager, "async_send", namespace["async_send"])
    with pytest.raises(AssertionError, match="public queue admission must authorize"):
        await test_direct_send_cannot_bypass_control_permission(hass, admission)


async def test_post_permission_oracle_detects_removed_state_recheck(
    hass, admission, monkeypatch
):
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(intercom.IntercomManager.async_send))
    )
    function = tree.body[0]
    checks = [
        node
        for node in function.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "not self._enabled"
    ]
    assert len(checks) == 2
    function.body.remove(checks[1])
    namespace = dict(vars(intercom))
    exec(
        compile(
            ast.fix_missing_locations(tree), "<removed-post-permission-check>", "exec"
        ),
        namespace,
    )
    monkeypatch.setattr(intercom.IntercomManager, "async_send", namespace["async_send"])
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        await test_permission_await_rechecks_admission_without_expanding_targets(
            hass, admission, "disabled", monkeypatch
        )
