"""Overnight overdue delayed-call backlog through independent booted HA processes."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from tests_real_ha import test_delayed_tool_process_restart as base
from tests_real_ha.process_harness import run_python_child
from tests_stress.conftest import record

PHASE = "EOAI_DELAYED_BACKLOG_PHASE"
ROOT_ENV = "EOAI_DELAYED_BACKLOG_CONFIG"
COUNT_ENV = "EOAI_DELAYED_BACKLOG_COUNT"


async def schedule_backlog(hass, agent, user, function_tool):
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    const = importlib.import_module(f"custom_components.{base.DOMAIN}.const")
    manager = hass.data[base.DOMAIN]["delayed_tool_manager"]
    disabled = deepcopy(base._tool_config())
    disabled["spec"]["name"] += "_disabled"
    subentry = agent.subentry
    options = dict(subentry.data)
    options[const.CONF_API_MODE] = "responses"
    options[const.CONF_CHAT_MODEL] = "gpt-5.6"
    options[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump([base._tool_config(), disabled])
    hass.config_entries.async_update_subentry(agent.entry, subentry, data=options)
    agent_config = importlib.import_module(
        f"custom_components.{base.DOMAIN}.agent_config"
    )
    current = agent_config.configured_function_tools_from_data(options)
    configured_disabled = next(
        tool for tool in current if tool["spec"]["name"] == disabled["spec"]["name"]
    )
    function_tool = next(
        tool for tool in current if tool["spec"]["name"] == base._TOOL_NAME
    )
    revoked = await hass.auth.async_create_user("Backlog revoked user")
    expected = [base._MARKER]
    count = int(os.environ[COUNT_ENV])
    for index in range(count - 1):
        tool = configured_disabled if index % 5 == 0 else function_tool
        actor = revoked if index % 5 == 1 else user
        marker = f"backlog-{index:03d}"
        await agent._execute_function_tool(
            tool,
            llm.ToolInput(
                id=f"call-{marker}",
                tool_name=tool["spec"]["name"],
                tool_args={"marker": marker, "delay": {"seconds": 3600}},
                external=True,
            ),
            SimpleNamespace(
                context=Context(user_id=actor.id), device_id=base._DEVICE_ID
            ),
            [],
        )
        if index % 5 not in {0, 1}:
            expected.append(marker)
    # Settle the initial configuration save before applying revocation. This makes
    # the cold-process check independent of HA's delayed-save timing.
    await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
    await hass.auth.async_remove_user(revoked)
    disabled["enabled"] = False
    # HA retains a view of the supplied mapping. Mutating it in place makes its
    # equality check miss the update and can leave the saved tool enabled.
    options = dict(agent.subentry.data)
    options[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump([base._tool_config(), disabled])
    hass.config_entries.async_update_subentry(agent.entry, agent.subentry, data=options)
    assert len(manager._records) == count
    root = Path(os.environ[ROOT_ENV])
    (root / "backlog-expected.json").write_text(
        json.dumps({"markers": expected, "user_id": user.id, "count": count})
    )


async def recover(root):
    from homeassistant import bootstrap, runner

    sys.path.insert(0, str(root))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(root), skip_pip=False)
    )
    assert hass is not None
    await base._register_execution_probe(hass, root)
    # Keep selected real service actions in-flight while independent HA traffic runs.
    service = hass.services.async_services()[base._SERVICE_DOMAIN][base._SERVICE_NAME]
    original_probe = service.job.target
    slow_started, slow_release = asyncio.Event(), asyncio.Event()
    active, peak, slow_admitted = 0, 0, 0

    async def slow_probe(call):
        nonlocal active, peak, slow_admitted
        active += 1
        peak = max(peak, active)
        try:
            if slow_admitted < 3:
                slow_admitted += 1
                slow_started.set()
                await slow_release.wait()
            await original_probe(call)
        finally:
            active -= 1

    hass.services.async_register(base._SERVICE_DOMAIN, base._SERVICE_NAME, slow_probe)
    manager = hass.data[base.DOMAIN]["delayed_tool_manager"]
    resolve = manager._resolve_agent
    transient_failures = 0

    def transient_resolution(*args):
        nonlocal transient_failures
        if os.environ[PHASE] != "verify-no-replay" and transient_failures < 6:
            transient_failures += 1
            return None  # Supported pre-dispatch agent reload/unavailability retry.
        return resolve(*args)

    manager._resolve_agent = transient_resolution
    await hass.async_start()
    lags = []

    async def sample_lag():
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(0.05)
            lags.append(max(0, loop.time() - start - 0.05))

    sampler = asyncio.create_task(sample_lag())
    try:
        base._assert_source_component(root)
        from custom_components.extended_openai_conversation_responses.management_ui import (
            async_management_command,
        )
        from homeassistant.components import conversation
        from homeassistant.core import Context
        from tests_real_ha.test_provider_wire_e2e import (
            _chat_sse_text,
            _install_wire,
            _responses_sse_text,
            _speech,
        )

        expected = json.loads((root / "backlog-expected.json").read_text())
        manager = hass.data[base.DOMAIN]["delayed_tool_manager"]
        if os.environ[PHASE] != "verify-no-replay":
            await asyncio.wait_for(slow_started.wait(), 20)
            assert active > 0 and manager._records and manager._tasks
            assert len(manager._tasks) <= expected["count"]
            assert transient_failures == 6
            assert any(record.retry_count > 0 for record in manager._records.values())
        during_pending = len(manager._records)
        entry = hass.config_entries.async_entries(base.DOMAIN)[0]
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        with pytest.MonkeyPatch.context() as patch:
            mode = agent.subentry.data.get("api_mode", "responses")
            reply = _responses_sse_text if mode == "responses" else _chat_sse_text
            wire = _install_wire(patch, agent, [reply("Backlog Assist healthy")])
            result = await asyncio.wait_for(
                conversation.async_converse(
                    hass=hass,
                    text="After backlog",
                    conversation_id=None,
                    context=Context(user_id=expected["user_id"]),
                    language="en",
                    agent_id=entry.entry_id,
                ),
                15,
            )
            assert _speech(result) == "Backlog Assist healthy"
            assert len(wire.requests) == 1
        summary = await asyncio.wait_for(
            async_management_command(
                hass,
                expected["user_id"],
                True,
                {
                    "section": "overview",
                    "action": "summary",
                    "entry_id": entry.entry_id,
                    "subentry_id": agent.subentry.subentry_id,
                },
            ),
            15,
        )
        assert summary["agent"]["entry_id"] == entry.entry_id
        assert summary["agent"]["subentry_id"] == agent.subentry.subentry_id
        assert summary["load_errors"] == []
        if os.environ[PHASE] == "partial-terminate":
            async with asyncio.timeout(15):
                while not base._read_executions(root):
                    await asyncio.sleep(0.02)
            payload = base._read_store(root)["data"]["calls"]
            assert any(call["status"] == "executing" for call in payload)
            assert any(call["status"] == "pending" for call in payload)
            (root / "backlog-partial-proof.json").write_text(
                json.dumps(
                    {
                        "completed": len(base._read_executions(root)),
                        "pending": during_pending,
                        "active": active,
                        "foreground_reads": 2,
                        "retryable_failures": transient_failures,
                    }
                )
            )
            os._exit(73)  # Genuine termination: no HA stop/finalize callbacks.
        slow_release.set()
        async with asyncio.timeout(60):
            while manager._records or manager._tasks:
                await asyncio.sleep(0.05)
        actual_markers = Counter(item["marker"] for item in base._read_executions(root))
        expected_markers = Counter(expected["markers"])
        assert actual_markers == expected_markers, {
            "missing": expected_markers - actual_markers,
            "unexpected": actual_markers - expected_markers,
        }
        assert all(
            item["user_id"] == expected["user_id"]
            for item in base._read_executions(root)
        )
        assert base._read_store(root)["data"]["calls"] == []
        await asyncio.sleep(0.1)
        assert lags and max(lags) < 5, lags
        (root / f"backlog-health-{os.environ[PHASE]}.json").write_text(
            json.dumps(
                {
                    "executions": len(expected["markers"]),
                    "discarded": expected["count"] - len(expected["markers"]),
                    "max_loop_lag_seconds": max(lags),
                    "pending": len(manager._records),
                    "waiters": len(manager._tasks),
                    "assist_conversations": 1,
                    "management_reads": 1,
                    "foreground_pending": during_pending,
                    "peak_active": peak,
                    "retryable_failures": transient_failures,
                }
            )
        )
    finally:
        slow_release.set()
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
        await hass.async_stop()


async def child():
    root = Path(os.environ[ROOT_ENV])
    if os.environ[PHASE] == "schedule":
        await base._create_entry_and_schedule(
            root, delay_seconds=3600, after_schedule=schedule_backlog
        )
    else:
        await recover(root)


@pytest.mark.timeout(360)
@pytest.mark.parametrize(
    "partial_termination", [False, True], ids=["slow-drain", "partial-termination"]
)
def test_overdue_backlog_drains_once_across_two_restarts(
    tmp_path,
    stress_scale,
    stress_trace,
    socket_enabled,
    unused_tcp_port,
    partial_termination,
):
    count = 50 if stress_scale == 1 else 100
    root = tmp_path / "ha-config"
    target = root / "custom_components" / base.DOMAIN
    target.parent.mkdir(parents=True)
    base._stage_component(
        Path(__file__).resolve().parents[1] / "custom_components" / base.DOMAIN, target
    )
    (root / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Overnight delayed backlog\nhttp:\n  server_host: 127.0.0.1\n  server_port: {unused_tcp_port}\n"
    )
    phases = (
        ("schedule", "partial-terminate", "recover", "verify-no-replay")
        if partial_termination
        else ("schedule", "recover", "verify-no-replay")
    )
    for phase in phases:
        result = run_python_child(
            __file__,
            cwd=root,
            extra_env={PHASE: phase, ROOT_ENV: str(root), COUNT_ENV: str(count)},
            timeout=90,
        )
        if phase == "partial-terminate":
            assert result.returncode == 73, result.stdout + result.stderr
            proof = json.loads((root / "backlog-partial-proof.json").read_text())
            assert (
                proof["completed"] > 0
                and proof["active"] > 0
                and proof["foreground_reads"] == 2
            )
            # Cold recovery deliberately discards durable executing records; no
            # replay requirement is imposed on ambiguous already-started effects.
            completed = {item["marker"] for item in base._read_executions(root)}
            ambiguous = {
                call["arguments"]["marker"]
                for call in base._read_store(root)["data"]["calls"]
                if call["status"] == "executing"
            } - completed
            expected_path = root / "backlog-expected.json"
            expected = json.loads(expected_path.read_text())
            expected["markers"] = [
                marker for marker in expected["markers"] if marker not in ambiguous
            ]
            expected_path.write_text(json.dumps(expected))
            record(
                stress_trace,
                "summary",
                layer="process",
                delayed_partial_termination_cases=1,
                delayed_foreground_during_backlog=2,
                ambiguous_started_calls=len(ambiguous),
            )
            continue
        base._assert_child_ok(result, phase)
        if phase == "schedule":
            entries = json.loads(
                (root / ".storage" / "core.config_entries").read_text()
            )
            entry = next(
                item
                for item in entries["data"]["entries"]
                if item["domain"] == base.DOMAIN
            )
            subentry = next(
                item
                for item in entry["subentries"]
                if item["subentry_type"] == "conversation"
            )
            tools = yaml.safe_load(subentry["data"]["functions"])
            disabled = next(
                tool for tool in tools if tool["spec"]["name"].endswith("_disabled")
            )
            assert disabled["enabled"] is False
            payload = base._read_store(root)
            assert len(payload["data"]["calls"]) == count
            for call in payload["data"]["calls"]:
                call["due_at"] = base._PAST_DUE
            base._write_store(root, payload)
        else:
            health = json.loads((root / f"backlog-health-{phase}.json").read_text())
            record(
                stress_trace,
                "summary",
                campaign_action="delayed_backlog",
                layer="process",
                phase=phase,
                pending_calls=count,
                delayed_backlog_restarts=1,
                delayed_backlog_calls=count if phase == "recover" else 0,
                delayed_backlog_executions=health["executions"]
                if phase == "recover"
                else 0,
                process_restarts=1,
                delayed_foreground_during_backlog=2
                if health["foreground_pending"]
                else 0,
                delayed_retryable_pre_dispatch_failures=health["retryable_failures"],
                **health,
            )


if __name__ == "__main__" and os.environ.get(PHASE):
    asyncio.run(child())
