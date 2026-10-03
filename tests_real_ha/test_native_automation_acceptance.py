"""A native automation tool must survive genuine HA validation and reload."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockUser
import voluptuous as vol
import yaml

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

_TOOL = {
    "spec": {
        "name": "acceptance_add_automation",
        "description": "Create one real Home Assistant automation.",
        "parameters": {
            "type": "object",
            "properties": {"automation_config": {"type": "string"}},
            "required": ["automation_config"],
        },
    },
    "function": {"type": "native", "name": "add_automation"},
    "enabled": True,
}


def _automation(alias: str, event: str, marker: str) -> dict:
    return {
        "alias": alias,
        "triggers": [{"trigger": "event", "event_type": event}],
        "actions": [{"action": "acceptance_probe.record", "data": {"marker": marker}}],
    }


async def _call_tool(hass, agent, user, automation_config: str):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        configured_function_tools_from_data,
    )

    tool = next(
        item
        for item in configured_function_tools_from_data(agent.subentry.data)
        if item["spec"]["name"] == _TOOL["spec"]["name"]
    )
    return tool_result_data(
        await agent._execute_function_tool(
            tool,
            llm.ToolInput(
                id="automation-acceptance-call",
                tool_name=_TOOL["spec"]["name"],
                tool_args={"automation_config": automation_config},
                external=True,
            ),
            SimpleNamespace(context=Context(user_id=user.id), device_id=None),
            [],
        )
    )


@pytest.mark.asyncio
async def test_native_add_automation_validates_reloads_and_triggers_once(
    hass: HomeAssistant,
) -> None:
    config_dir = hass.config.config_dir
    from pathlib import Path

    automation_file = Path(config_dir) / "automations.yaml"
    configuration_file = Path(config_dir) / "configuration.yaml"
    original = _automation(
        "Existing acceptance", "existing_acceptance_event", "existing"
    )
    automation_file.write_text(yaml.safe_dump([original]), encoding="utf-8")
    configuration_file.write_text(
        "automation: !include automations.yaml\n", encoding="utf-8"
    )

    calls: list[str] = []

    async def record(call) -> None:
        calls.append(call.data["marker"])

    hass.services.async_register("acceptance_probe", "record", record)
    assert await async_setup_component(hass, "automation", {"automation": [original]})
    await hass.async_block_till_done()

    entry = _make_entry(
        "Automation Tool Acceptance",
        include_ai_task=False,
        conversation_options={CONF_FUNCTION_TOOLS: yaml.safe_dump([_TOOL])},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    user = MockUser(id="automation-acceptance-admin", is_owner=True)
    user.add_to_hass(hass)

    hass.bus.async_fire("existing_acceptance_event")
    await hass.async_block_till_done()
    assert calls == ["existing"]

    added = _automation("New acceptance", "new_acceptance_event", "new")
    result = await _call_tool(hass, agent, user, yaml.safe_dump(added))
    assert result == {"result": "Success"}
    saved = yaml.safe_load(automation_file.read_text(encoding="utf-8"))
    assert [item["alias"] for item in saved] == [
        "Existing acceptance",
        "New acceptance",
    ]
    hass.bus.async_fire("new_acceptance_event")
    hass.bus.async_fire("existing_acceptance_event")
    await hass.async_block_till_done()
    assert calls == ["existing", "new", "existing"]

    await hass.services.async_call("automation", "reload", blocking=True)
    hass.bus.async_fire("new_acceptance_event")
    await hass.async_block_till_done()
    assert calls == ["existing", "new", "existing", "new"]

    before_invalid = automation_file.read_bytes()
    invalid = {
        "alias": "Invalid acceptance",
        "triggers": [{"trigger": "event"}],
        "actions": added["actions"],
    }
    with pytest.raises(vol.Invalid):
        await _call_tool(hass, agent, user, yaml.safe_dump(invalid))
    assert automation_file.read_bytes() == before_invalid
    hass.bus.async_fire("new_acceptance_event")
    hass.bus.async_fire("existing_acceptance_event")
    await hass.async_block_till_done()
    assert calls == ["existing", "new", "existing", "new", "new", "existing"]


@pytest.mark.parametrize("phase", ["replace", "reload", "rollback"])
async def test_cancelled_native_automation_retains_ownership_until_settled(
    hass, monkeypatch, phase
):
    """Real executor, YAML validation and reload retain a cancelled transaction."""
    import asyncio
    from pathlib import Path
    import threading

    from custom_components.extended_openai_conversation_responses.functions import (
        native,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.lock_probe import LockProbe

    path = Path(hass.config.config_dir) / "automations.yaml"
    original = _automation("Original", "audit_original", "original")
    path.write_text(yaml.safe_dump([original]), encoding="utf-8")
    Path(hass.config.config_dir, "configuration.yaml").write_text(
        "automation: !include automations.yaml\n", encoding="utf-8"
    )
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("acceptance_probe", "record", record)
    assert await async_setup_component(hass, "automation", {"automation": [original]})
    entry = _make_entry(
        "Cancellation Automation",
        include_ai_task=False,
        conversation_options={CONF_FUNCTION_TOOLS: yaml.safe_dump([_TOOL])},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    user = MockUser(id="automation-cancel-owner", is_owner=True)
    user.add_to_hass(hass)
    probe = LockProbe(asyncio.Lock())
    hass.data[native._AUTOMATION_WRITE_LOCK_KEY] = probe
    worker_entered = threading.Event()
    worker_release = threading.Event()
    reload_entered = asyncio.Event()
    reload_release = asyncio.Event()
    replace = native.os.replace
    replacements = 0

    def hold_replace(src, dst):
        nonlocal replacements
        if Path(dst) == path:
            replacements += 1
            if (phase == "replace" and replacements == 1) or (
                phase == "rollback" and replacements == 2
            ):
                worker_entered.set()
                assert worker_release.wait(15)
        return replace(src, dst)

    monkeypatch.setattr(native.os, "replace", hold_replace)
    real_call = type(hass.services).async_call
    reloads = 0

    async def hold_reload(registry, domain, service, *args, **kwargs):
        nonlocal reloads
        if domain == "automation" and service == "reload":
            reloads += 1
            if reloads == 1 and phase == "rollback":
                raise HomeAssistantError("controlled initial reload failure")
            if reloads == 1 and phase == "reload":
                reload_entered.set()
                await reload_release.wait()
        return await real_call(registry, domain, service, *args, **kwargs)

    monkeypatch.setattr(type(hass.services), "async_call", hold_reload)

    async def invoke(alias, event, marker):
        from custom_components.extended_openai_conversation_responses.function_execution import (
            propagate_function_execution_errors,
        )

        with propagate_function_execution_errors():
            return await _call_tool(
                hass, agent, user, yaml.safe_dump(_automation(alias, event, marker))
            )

    older = asyncio.create_task(invoke("Older", "audit_older", "older"))
    newer = None
    try:
        assert await probe.next_attempt() is older
        if phase == "reload":
            await asyncio.wait_for(reload_entered.wait(), 10)
        else:
            assert await asyncio.to_thread(worker_entered.wait, 10)
        older.cancel()
        newer = asyncio.create_task(invoke("Newer", "audit_newer", "newer"))
        assert await probe.next_attempt() is newer
        assert not newer.done()
        assert not older.done()
        older.cancel()  # repeated cancellation must not release transaction ownership
    finally:
        worker_release.set()
        reload_release.set()
        outcomes = await asyncio.gather(
            older, *([newer] if newer else []), return_exceptions=True
        )
    if phase == "rollback":
        # A settled native failure retains precedence over deferred cancellation.
        assert isinstance(outcomes[0], HomeAssistantError)
        assert "controlled initial reload failure" in str(outcomes[0])
    else:
        assert isinstance(outcomes[0], asyncio.CancelledError)
    assert outcomes[1] == {"result": "Success"}
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = (
        ["Original", "Newer"] if phase == "rollback" else ["Original", "Older", "Newer"]
    )
    assert [item["alias"] for item in saved] == expected
    hass.bus.async_fire("audit_original")
    hass.bus.async_fire("audit_older")
    hass.bus.async_fire("audit_newer")
    await hass.async_block_till_done()
    assert sorted(effects) == (
        ["newer", "original"] if phase == "rollback" else ["newer", "older", "original"]
    )
    from functools import partial
    import json
    import subprocess
    import sys

    child = await hass.async_add_executor_job(
        partial(
            subprocess.run,
            [
                sys.executable,
                str(Path(__file__).with_name("automation_fresh_process.py")),
                str(path),
                str(Path(hass.config.config_dir) / "fresh-process"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    )
    assert child.returncode == 0, child.stderr[-5000:]
    fresh = next(
        line
        for line in child.stdout.splitlines()
        if line.startswith("AUTOMATION_RECOVERY_PROBE=")
    )
    assert json.loads(fresh.split("=", 1)[1]) == sorted(effects)

    # Prove a fresh native load reads the settled file and both surviving entries.
    await hass.services.async_call("automation", "reload", blocking=True)
    effects.clear()
    hass.bus.async_fire("audit_newer")
    await hass.async_block_till_done()
    assert effects == ["newer"]
