"""Literal command, authority, persistence and upgrade contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import broadcast_command
from custom_components.extended_openai_conversation_responses.intercom import (
    IntercomManager,
)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Broadcast dinner is ready", "dinner is ready"),
        ("  bRoAdCaSt \t Dinner  is ready. \n", "Dinner  is ready."),
        ("Broadcast\nhello", "hello"),
        ("Broadcast kitchen is open", "kitchen is open"),
        ("Broadcast dinner is ready in ten minutes", "dinner is ready in ten minutes"),
        ("Broadcast tonight's dinner is ready", "tonight's dinner is ready"),
    ],
)
def test_literal_message(text, message):
    assert broadcast_command.broadcast_message(text) == message


@pytest.mark.parametrize(
    "text",
    [
        None,
        7,
        "",
        "  ",
        "Broadcast",
        "Broadcast \t\n",
        "Broadcasting hello",
        "please Broadcast dinner",
        "tell everyone dinner",
        "announce dinner",
        "send dinner",
        "Broadcast to kitchen saying hello",
        "Broadcast to nowhere hello",
        "Broadcast TO: kitchen hello",
        "Broadcast a message to kitchen hello",
        "Broadcast in ten minutes dinner",
        "Broadcast at noon dinner",
        "Broadcast after ten minutes dinner",
        "Broadcast for tomorrow dinner",
    ],
)
def test_unmatched_or_reserved_syntax(text):
    assert broadcast_command.broadcast_message(text) is None


def _input(text="Broadcast Dinner"):
    return SimpleNamespace(
        text=text,
        context=Context(user_id="caller"),
        language="en",
        satellite_id="assist_satellite.hall",
        device_id="hall-device",
    )


@pytest.mark.parametrize(
    ("guest", "options", "text"),
    [
        (True, {}, "Broadcast Dinner"),
        (False, {"local_intent_exclusions": ["HassBroadcast"]}, "Broadcast Dinner"),
        (False, {}, "turn on kitchen lights"),
        (False, {}, "Broadcast to kitchen hello"),
    ],
)
async def test_no_manager_or_storage_for_ineligible_request(
    hass, monkeypatch, guest, options, text
):
    get = AsyncMock(side_effect=AssertionError("must remain lazy"))
    monkeypatch.setattr(broadcast_command, "async_get_intercom", get)
    assert (
        await broadcast_command.async_try_broadcast_command(
            hass,
            _input(text),
            options,
            guest_active=guest,
            live_guest_active=lambda: (_ for _ in ()).throw(
                AssertionError("ineligible request must remain lazy")
            ),
        )
        is None
    )
    get.assert_not_awaited()


@pytest.mark.parametrize("master", [False, True])
@pytest.mark.parametrize("shortcut", [False, True])
@pytest.mark.parametrize("local_intents", [False, True])
async def test_independent_switches_and_real_origin(
    hass, monkeypatch, master, shortcut, local_intents
):
    send = AsyncMock()
    manager = SimpleNamespace(
        enabled=master, builtin_voice_command=shortcut, async_send=send
    )
    monkeypatch.setattr(
        broadcast_command, "async_get_intercom", AsyncMock(return_value=manager)
    )
    user_input = _input()
    result = await broadcast_command.async_try_broadcast_command(
        hass, user_input, {"local_intents_enabled": local_intents}, guest_active=False
    )
    if master and shortcut:
        assert result.intent_name == "ExtendedBroadcast"
        assert result.response.speech["plain"]["speech"] == "Broadcast queued."
        send.assert_awaited_once_with(
            "Dinner",
            whole_home=True,
            context=user_input.context,
            origin_entity_id="assist_satellite.hall",
            origin_device_id="hall-device",
            source="local_voice",
            _admission_started=ANY,
        )
    else:
        assert result is None
        send.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        "Permission denied",
        "No matching speakers",
        "Queue full",
        "delivery setup failed",
    ],
)
async def test_matched_admission_errors_propagate_once(hass, monkeypatch, failure):
    send = AsyncMock(side_effect=HomeAssistantError(failure))
    manager = SimpleNamespace(enabled=True, builtin_voice_command=True, async_send=send)
    monkeypatch.setattr(
        broadcast_command, "async_get_intercom", AsyncMock(return_value=manager)
    )
    with pytest.raises(HomeAssistantError, match=failure):
        await broadcast_command.async_try_broadcast_command(
            hass, _input(), {}, guest_active=False
        )
    assert send.await_count == 1


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {"enabled": False},
        {"enabled": True},
        {"enabled": True, "builtin_voice_command": False, "future_setting": "keep"},
    ],
)
async def test_legacy_defaults_and_reload_preserve_all_settings(hass, stored):
    record = dict(stored) if stored else None

    async def save(value):
        nonlocal record
        record = dict(value)

    store = SimpleNamespace(
        async_load=AsyncMock(side_effect=lambda: record),
        async_save=AsyncMock(side_effect=save),
    )
    manager = IntercomManager(hass)
    manager._store = store
    await manager.async_initialize()
    master = bool(stored and stored.get("enabled"))
    assert manager.enabled is master
    assert manager.builtin_voice_command is (stored or {}).get(
        "builtin_voice_command", True
    )
    await manager.async_set_builtin_voice_command(False)
    assert manager.enabled is master
    await manager.async_set_enabled(not master)
    assert manager.builtin_voice_command is False
    reloaded = IntercomManager(hass)
    reloaded._store = store
    await reloaded.async_initialize()
    assert reloaded.enabled is not master
    assert reloaded.builtin_voice_command is False
    if stored and "future_setting" in stored:
        assert record["future_setting"] == "keep"


@pytest.mark.parametrize("first", ["master", "shortcut"])
async def test_concurrent_updates_serialize_the_entire_record(hass, first):
    manager = IntercomManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"enabled": False})
    )
    await manager.async_initialize()
    entered, release = asyncio.Event(), asyncio.Event()
    saves = []

    async def save(record):
        saves.append(dict(record))
        entered.set()
        await release.wait()

    manager._store.async_save = AsyncMock(side_effect=save)
    master = lambda: manager.async_set_enabled(True)
    shortcut = lambda: manager.async_set_builtin_voice_command(False)
    a, b = (master, shortcut) if first == "master" else (shortcut, master)
    task_a = asyncio.create_task(a())
    await entered.wait()
    task_b = asyncio.create_task(b())
    await asyncio.sleep(0)
    assert len(saves) == 1
    assert manager.enabled is False and manager.builtin_voice_command is True
    release.set()
    await asyncio.gather(task_a, task_b)
    assert saves[-1] == {"enabled": True, "builtin_voice_command": False}
    assert manager.enabled is True and manager.builtin_voice_command is False


@pytest.mark.parametrize(
    "setter", ["async_set_enabled", "async_set_builtin_voice_command"]
)
async def test_failed_write_keeps_effective_settings(hass, setter):
    manager = IntercomManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"enabled": True}),
        async_save=AsyncMock(side_effect=HomeAssistantError("disk full")),
    )
    await manager.async_initialize()
    with pytest.raises(HomeAssistantError, match="disk full"):
        await getattr(manager, setter)(False)
    assert manager.enabled is True and manager.builtin_voice_command is True
    assert manager._settings == {"enabled": True}


@pytest.mark.parametrize(
    "targets", [[], ["assist_satellite.denied"], ["assist_satellite.kitchen"]]
)
async def test_real_queue_authority_and_origin(hass, monkeypatch, targets):
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "resolve_targets", lambda **kw: targets)
    monkeypatch.setattr(manager, "_schedule_drain", lambda _: None)
    monkeypatch.setattr(
        broadcast_command, "async_get_intercom", AsyncMock(return_value=manager)
    )
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(
            is_active=True,
            is_admin=False,
            permissions=SimpleNamespace(
                check_entity=lambda e, _: e.endswith("kitchen")
            ),
        )
    )
    if not targets or targets[0].endswith("denied"):
        with pytest.raises(HomeAssistantError):
            await broadcast_command.async_try_broadcast_command(
                hass, _input(), {}, guest_active=False
            )
        assert not manager.history() and not manager._queues
    else:
        await broadcast_command.async_try_broadcast_command(
            hass, _input(), {}, guest_active=False
        )
        assert len(manager.history()) == len(manager._queues[targets[0]]) == 1
        assert manager._history[0].context.user_id == "caller"
        assert manager.history()[0]["origin_device_id"] == "hall-device"
