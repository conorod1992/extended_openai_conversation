"""Enhanced-only genuine Recorder and automation lifecycle regressions."""

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from homeassistant.components import automation, conversation, recorder
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from tests_real_ha.test_native_recorder_acceptance import _EXPOSED
from tests_stress.conftest import record


@pytest.mark.parametrize(
    "layout", ["standard", "standard-disabled", "inline", "alternate"]
)
async def test_native_automation_success_requires_generated_id_loaded(
    hass, layout, stress_trace
):
    """Reload success alone cannot acknowledge an automation HA never loaded."""
    from pytest_homeassistant_custom_component.common import MockUser
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_FUNCTION_TOOLS,
        EVENT_AUTOMATION_REGISTERED,
    )
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
    from tests_real_ha.test_native_automation_acceptance import (
        _TOOL,
        _automation,
        _call_tool,
    )

    directory = Path(hass.config.config_dir)
    assert await async_setup_component(
        hass,
        "recorder",
        {"recorder": {"db_url": f"sqlite:///{directory / 'layout.db'}"}},
    )
    path = directory / "automations.yaml"
    config_path = directory / "configuration.yaml"
    alternate_path = directory / "alternate.yaml"
    original = _automation("Layout original", "layout_original", "original")
    original["id"] = "layout-original"
    path.write_text(yaml.safe_dump([original]))
    alternate_path.write_text(yaml.safe_dump([original]))
    if layout == "inline":
        config_path.write_text(yaml.safe_dump({"automation": [original]}))
    else:
        config_path.write_text(
            f"automation: !include {'alternate.yaml' if layout == 'alternate' else 'automations.yaml'}\n"
        )
    before, config_before, alternate_before = (
        path.read_bytes(),
        config_path.read_bytes(),
        alternate_path.read_bytes(),
    )
    effects, registered = [], []

    async def effect(call):
        effects.append(call.data["marker"])

    hass.services.async_register("acceptance_probe", "record", effect)
    hass.bus.async_listen(
        EVENT_AUTOMATION_REGISTERED, lambda event: registered.append(event.data)
    )
    assert await async_setup_component(hass, "automation", {"automation": [original]})
    entry = _make_entry(
        "Automation layout",
        include_ai_task=False,
        conversation_options={CONF_FUNCTION_TOOLS: yaml.safe_dump([_TOOL])},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    user = MockUser(id="automation-layout-owner", is_owner=True)
    user.add_to_hass(hass)
    added = _automation("Layout added", "layout_added", "added")
    disabled = layout == "standard-disabled"
    if disabled:
        added["initial_state"] = False
    rejected = layout in {"inline", "alternate"}
    if rejected:
        result = await _call_tool(hass, agent, user, yaml.safe_dump(added))
        failure = result["result"]
        assert failure["status"] == "error"
        assert "not loaded" in failure["error"]
        assert "automation: !include automations.yaml" in failure["error"]
        assert path.read_bytes() == before, "Unloaded automation must be rolled back"
        assert not registered
        assert [
            entity.unique_id for entity in hass.data[automation.DOMAIN].entities
        ] == ["layout-original"]
    else:
        assert await _call_tool(hass, agent, user, yaml.safe_dump(added)) == {
            "result": "Success"
        }
        saved = yaml.safe_load(path.read_text())
        generated_id = saved[-1]["id"]
        loaded = next(
            entity
            for entity in hass.data[automation.DOMAIN].entities
            if entity.unique_id == generated_id
        )
        assert hass.states.get(loaded.entity_id).state == ("off" if disabled else "on")
        await hass.async_block_till_done()
        assert len(registered) == 1
    assert config_path.read_bytes() == config_before
    assert alternate_path.read_bytes() == alternate_before
    hass.bus.async_fire("layout_added")
    hass.bus.async_fire("layout_original")
    await hass.async_block_till_done()
    assert sorted(effects) == (
        ["original"] if rejected or disabled else ["added", "original"]
    )
    if disabled:
        await hass.services.async_call(
            "automation", "turn_on", {"entity_id": loaded.entity_id}, blocking=True
        )
        hass.bus.async_fire("layout_added")
        await hass.async_block_till_done()
        assert effects == ["original", "added"], (
            "Disabled-but-loaded automation must be executable when enabled"
        )
    record(
        stress_trace,
        "summary",
        native_automation_layout_cases=1,
        native_automation_load_rejections=int(rejected),
        native_disabled_automation_loads=int(disabled),
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_native_history_worker_owns_session_until_query_finishes(
    hass, tmp_path, monkeypatch, cancel, stress_trace
):
    """Cancellation cannot return a genuine Recorder connection before its worker."""
    import asyncio
    import threading
    from sqlalchemy import event
    from custom_components.extended_openai_conversation_responses.functions import (
        native,
    )

    assert await async_setup_component(
        hass,
        "recorder",
        {"recorder": {"db_url": f"sqlite:///{tmp_path / 'cancellation.db'}"}},
    )
    started = dt_util.utcnow() - timedelta(seconds=1)
    hass.states.async_set("sensor.recorder_acceptance", "recorded-before-cancel")
    await hass.async_block_till_done()
    instance = recorder.get_instance(hass)
    await instance.async_block_till_done()
    args = {
        "entity_ids": ["sensor.recorder_acceptance"],
        "start_time": started.isoformat(),
        "end_time": dt_util.utcnow().isoformat(),
        "significant_changes_only": False,
        "include_start_time_state": False,
    }
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    owner = {}
    checkouts, checkins = [], []
    original = native.recorder_history.get_significant_states_with_session

    def gated_query(*args, **kwargs):
        if owner:
            return original(*args, **kwargs)
        owner.update(thread=threading.get_ident(), session=args[1])
        result = original(*args, **kwargs)
        owner["rows"] = [
            [item.state if hasattr(item, "state") else item["state"] for item in rows]
            for rows in result.values()
        ]
        entered.set()
        assert release.wait(15)
        return result

    def checkout(connection, *_args):
        if threading.get_ident() == owner.get("thread"):
            checkouts.append(id(connection))

    def checkin(connection, *_args):
        if id(connection) in checkouts:
            checkins.append((id(connection), threading.get_ident()))
            returned.set()

    monkeypatch.setattr(
        native.recorder_history, "get_significant_states_with_session", gated_query
    )
    event.listen(instance.engine, "checkout", checkout)
    event.listen(instance.engine, "checkin", checkin)
    task = asyncio.create_task(
        NativeFunction().get_history(hass, {}, args, None, _EXPOSED)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert owner["rows"] == [["recorded-before-cancel"]]
        assert checkouts and not checkins
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not checkins, (
                "Cancellation must not close the worker's active session"
            )
        release.set()
        if not cancel:
            assert [[row["state"] for row in rows] for rows in await task] == [
                ["recorded-before-cancel"]
            ]
        assert await asyncio.to_thread(returned.wait, 10), (
            "Resources must return without gc.collect()"
        )
        assert all(thread == owner["thread"] for _, thread in checkins)
        healthy = await NativeFunction().get_history(hass, {}, args, None, _EXPOSED)
        assert [[row["state"] for row in rows] for rows in healthy] == [
            ["recorded-before-cancel"]
        ]
        record(
            stress_trace,
            "summary",
            native_history_worker_settlements=1,
            native_history_cancelled_recoveries=int(cancel),
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        event.remove(instance.engine, "checkout", checkout)
        event.remove(instance.engine, "checkin", checkin)
