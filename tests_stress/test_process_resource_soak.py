"""Booted-process resource windows over actual fragmented provider traffic."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import gc
import importlib
from itertools import pairwise
import json
import logging
import os
from pathlib import Path
import random
import shutil
import stat
import statistics
import sys
from time import monotonic
from unittest.mock import AsyncMock, patch

import pytest

from tests_real_ha.process_harness import run_python_child
from tests_real_ha.test_packaged_process_restart import DOMAIN, _assert_packaged_module

if os.environ.get("EOAI_RESOURCE_SOAK_CHILD") != "1":
    # The fixtures import production modules. A child must first select its
    # staged payload in _booted_soak, rather than cache repository modules here.
    from tests_stress.conftest import record

_CHILD = "EOAI_RESOURCE_SOAK_CHILD"
_REPORT = "process-resource-windows.json"
_LIFETIME_FEATURES = (
    "memory",
    "knowledge",
    "rules",
    "functions",
    "ai_tasks",
    "archive",
    "maintenance",
    "expiry",
    "chat_completions",
    "responses",
)


def _sample_files(root):
    """Sample actual sizes once; concurrent atomic rename may remove an entry."""
    sampled = []
    vanished = 0
    for path in list(root.rglob("*")):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            vanished += 1
            continue
        if stat.S_ISREG(metadata.st_mode):
            sampled.append((path, metadata.st_size))
    return sampled, vanished


def test_resource_sampling_survives_real_atomic_rename(tmp_path, monkeypatch):
    permanent = tmp_path / "retained"
    permanent.write_bytes(b"retained")
    transient = tmp_path / "atomic.tmp"
    transient.write_bytes(b"committed")
    committed = tmp_path / "committed"
    original = Path.stat
    renamed = False

    def during_sample(path, *args, **kwargs):
        nonlocal renamed
        if path == transient and not renamed:
            renamed = True
            transient.replace(committed)
        return original(path, *args, **kwargs)

    with monkeypatch.context() as sampling:
        sampling.setattr(Path, "stat", during_sample)
        files, vanished = _sample_files(tmp_path)
    assert renamed and vanished == 1
    assert (permanent, 8) in files
    settled, vanished = _sample_files(tmp_path)
    assert set(settled) == {(permanent, 8), (committed, 9)} and vanished == 0


def _assert_populated_lifetime(report):
    assert report["management_transport"] == "authenticated_home_assistant_websocket"
    assert report["management_websocket_commands"] >= 20
    assert all(
        report["feature_activity"].get(name, 0) > 0 for name in _LIFETIME_FEATURES
    ), report
    if report["uninterrupted_runtime"]:
        assert report["config_entry_reloads"] == 0


@pytest.mark.parametrize("missing", (*_LIFETIME_FEATURES, "management", "reload"))
def test_populated_lifetime_rejects_missing_native_participation(missing):
    report = {
        "management_transport": "authenticated_home_assistant_websocket",
        "management_websocket_commands": 20,
        "feature_activity": dict.fromkeys(_LIFETIME_FEATURES, 1),
        "uninterrupted_runtime": True,
        "config_entry_reloads": 0,
    }
    _assert_populated_lifetime(report)
    if missing == "management":
        report["management_transport"] = "direct_manager"
    elif missing == "reload":
        report["config_entry_reloads"] = 1
    else:
        report["feature_activity"][missing] = 0
    with pytest.raises(AssertionError):
        _assert_populated_lifetime(report)


def _assert_resource_windows(windows):
    """Broad growth/stall tripwires, independent of hosted-runner microbenchmarks."""
    assert len(windows) >= 5, "warm baseline plus four exercised windows required"
    baseline, *later = windows
    ceilings = {
        "rss_bytes": 192 * 1024 * 1024,
        "descriptors": 32,
        "storage_bytes": 64 * 1024 * 1024,
        "storage_files": 128,
        "threads": 32,
        "executor_pending": 32,
        "eoai_tasks": 12,
        "subscriptions": 16,
        "transfer_files": 2,
    }
    # Last-window medians avoid treating one transient allocation as a leak.
    for key, ceiling in ceilings.items():
        values = [item[key] for item in later if item[key] is not None]
        if baseline[key] is not None and values:
            assert statistics.median(values[-2:]) - baseline[key] <= ceiling, (
                key,
                windows,
            )
        # Also reject persistent growth below the generous absolute ceiling when
        # every later window grows materially, rather than warming then plateauing.
        increments = [right - left for left, right in pairwise(values)]
        material = {
            "rss_bytes": 16 * 1024 * 1024,
            "storage_bytes": 8 * 1024 * 1024,
            "descriptors": 8,
            "storage_files": 24,
            "threads": 8,
            "executor_pending": 8,
            "eoai_tasks": 3,
            "subscriptions": 4,
            "transfer_files": 0,
        }[key]
        assert not increments or not all(delta > material for delta in increments), (
            key,
            windows,
        )
    for item in windows:
        assert item["background_failures"] == [], item
        assert item["sample_phase"] == "before_gc", item
    for item in later:
        assert item["loop_lag_max_seconds"] < 5, item
        assert item["assist_p95_seconds"] < 20, item
        assert item["management_p95_seconds"] < 15, item
        assert item["assist_turns"] >= 4 and item["management_operations"] >= 4, item


@pytest.mark.parametrize(
    "failure",
    [
        "rss_bytes",
        "descriptors",
        "storage_bytes",
        "executor_pending",
        "loop_lag",
        "assist",
        "management",
        "eoai_tasks",
        "subscriptions",
        "transfer_files",
        "background",
        "post_gc",
    ],
)
def test_resource_tripwires_reject_growth_or_lost_responsiveness(failure):
    baseline = {
        "rss_bytes": 100_000_000,
        "descriptors": 25,
        "storage_bytes": 1_000_000,
        "storage_files": 20,
        "threads": 8,
        "executor_pending": 0,
        "eoai_tasks": 2,
        "subscriptions": 8,
        "transfer_files": 0,
        "background_failures": [],
        "sample_phase": "before_gc",
        "loop_lag_max_seconds": 0.01,
        "assist_p95_seconds": 0.1,
        "management_p95_seconds": 0.01,
        "assist_turns": 4,
        "management_operations": 4,
    }
    windows = [dict(baseline) for _ in range(5)]
    if failure in {
        "rss_bytes",
        "descriptors",
        "storage_bytes",
        "executor_pending",
        "eoai_tasks",
        "subscriptions",
        "transfer_files",
    }:
        growth = {
            "rss_bytes": 80_000_000,
            "descriptors": 20,
            "storage_bytes": 40_000_000,
            "executor_pending": 20,
            "eoai_tasks": 8,
            "subscriptions": 10,
            "transfer_files": 2,
        }[failure]
        for index, item in enumerate(windows[1:], 1):
            item[failure] += growth * index
    elif failure == "background":
        windows[-1]["background_failures"] = ["Task exception was never retrieved"]
    elif failure == "post_gc":
        windows[-1]["sample_phase"] = "after_gc"
    else:
        key = {
            "loop_lag": "loop_lag_max_seconds",
            "assist": "assist_p95_seconds",
            "management": "management_p95_seconds",
        }[failure]
        windows[-1][key] = 30
    with pytest.raises(AssertionError):
        _assert_resource_windows(windows)
    # Modest runner variation and a stable warmed plateau remain acceptable.
    healthy = [dict(baseline) for _ in range(5)]
    for item in healthy[1:]:
        item["rss_bytes"] += 32_000_000
        item["assist_p95_seconds"] *= 1.15
    _assert_resource_windows(healthy)


async def _booted_soak(config_dir, seed, scale):
    from aiohttp import ClientSession, web
    import httpx
    import psutil

    from homeassistant import bootstrap, runner
    from homeassistant.components import ai_task, conversation
    from homeassistant.config_entries import SOURCE_USER, ConfigSubentry
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.core import Context
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import entity_registry as er

    # Staged production payload takes precedence over the repository test imports.
    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()
    from homeassistant.components.http.config import async_get_and_load_store

    # A fresh nondefault loopback port is HA's pending HTTP configuration. Promote
    # it exactly as HA's confirmation command does; otherwise its real five-minute
    # rollback watchdog restarts the harness before a long lifetime can be tested.
    http_store = await async_get_and_load_store(hass)
    if http_store.pending is not None:
        await http_store.async_promote_pending()
    assert http_store.pending is None and http_store.revert_deadline is None
    _assert_packaged_module(config_dir)
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _chat_sse_tool_call,
        _raw_client,
        _responses_sse_text,
        _responses_sse_tool_call,
        _speech,
    )

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    helpers = importlib.import_module(f"custom_components.{DOMAIN}.helpers")
    agent_module = importlib.import_module(f"custom_components.{DOMAIN}.conversation")
    transfers = importlib.import_module(f"custom_components.{DOMAIN}.backup_transfer")
    long_lifetime = os.environ.get("STRESS_CAMPAIGN") == "long-lifetime"
    uninterrupted = os.environ.get("EOAI_UNINTERRUPTED_SOAK") == "1"
    lifetime_started = monotonic()
    retention_callbacks = 0
    idle_seconds = 0.0
    expired_transfer_reclaims = 0
    background_failures = []
    previous_exception_handler = hass.loop.get_exception_handler()

    def background_failure(loop, context):
        background_failures.append(
            str(context.get("exception", context.get("message")))
        )
        if previous_exception_handler:
            previous_exception_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    hass.loop.set_exception_handler(background_failure)

    class BackgroundFailures(logging.Handler):
        def emit(self, event):
            message = event.getMessage()
            if (
                "Error doing job" in message
                or "Task exception was never retrieved" in message
            ):
                background_failures.append(message)

    failure_logs = BackgroundFailures(level=logging.ERROR)
    logging.getLogger().addHandler(failure_logs)
    original_interval = agent_module.async_track_time_interval

    def timed_retention(hass, callback, interval):
        if not long_lifetime or callback.__name__ != "_async_prune_archive_retention":
            return original_interval(hass, callback, interval)
        from datetime import timedelta

        assert interval == timedelta(days=1)

        async def observed(now):
            nonlocal retention_callbacks
            await callback(now)
            retention_callbacks += 1

        # Exercise the real scheduled callback and entity-owned cancellation.
        # Only the harness interval is accelerated; no claim of 24h elapsed time.
        return original_interval(hass, observed, timedelta(seconds=60))

    held_started, release_held = asyncio.Event(), asyncio.Event()
    provider_requests = 0
    config_entry_reloads = 0

    async def provider(request):
        nonlocal provider_requests
        provider_requests += 1
        body = await request.json()
        rows = body.get("messages", body.get("input", []))
        content = str(rows[-1].get("content", ""))
        if content == "Fail this transport":
            assert request.transport is not None
            request.transport.abort()
            return web.Response()
        payload = (
            _responses_sse_text
            if request.path.endswith("responses")
            else _chat_sse_text
        )("Process remains responsive.")
        if "Use lifetime function" in json.dumps(body):
            if "LIFETIME_FUNCTION_HEALTHY" not in json.dumps(rows):
                tools = body["tools"]
                names = [
                    tool.get("name", tool.get("function", {}).get("name"))
                    for tool in tools
                ]
                assert "lifetime_marker" in names
                payload = (
                    _responses_sse_tool_call(name="lifetime_marker", tool_arguments={})
                    if request.path.endswith("responses")
                    else _chat_sse_tool_call(name="lifetime_marker", arguments={})
                )
            else:
                feature_counts["functions"] += 1
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        if content == "Hold this stream":
            await response.write(payload[:40])
            held_started.set()
            await release_held.wait()
        with suppress(ConnectionResetError):
            for start in range(0, len(payload), 41):
                await response.write(payload[start : start + 41])
                await asyncio.sleep(0.005)
            await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", provider)
    app.router.add_post("/v1/responses", provider)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    client = httpx.AsyncClient(trust_env=False, timeout=15)
    config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
    process = psutil.Process()
    windows, lag_samples = [], []
    owner = await hass.auth.async_create_user("Process soak owner")
    assert owner.is_owner
    ws_session = ClientSession(trust_env=False)
    websocket = None
    ws_id = 0
    feature_counts = dict.fromkeys(_LIFETIME_FEATURES, 0)

    async def management_call(section, action, **payload):
        nonlocal ws_id
        ws_id += 1
        await websocket.send_json(
            {
                "id": ws_id,
                "type": f"{DOMAIN}/management",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "section": section,
                "action": action,
                **payload,
            }
        )
        response = await asyncio.wait_for(websocket.receive_json(), 15)
        assert response["id"] == ws_id and response["success"], response
        return response["result"]

    rng = random.Random(seed)
    lag_active = True

    async def responsiveness_probe():
        while lag_active:
            due = monotonic() + 0.05
            await asyncio.sleep(0.05)
            lag_samples.append(max(0, monotonic() - due))

    async def turn(text, identity=None):
        began = monotonic()
        result = await asyncio.wait_for(
            conversation.async_converse(
                hass=hass,
                text=text,
                conversation_id=identity,
                context=Context(user_id=owner.id),
                language="en",
                agent_id=active_agent_id,
            ),
            20,
        )
        return result, monotonic() - began

    async def management_step(number):
        began = monotonic()
        added = await management_call(
            "memories",
            "add",
            content=f"Soak preference {number}",
            category="preferences",
            key=f"soak_{number}",
        )
        rows = (await management_call("memories", "list"))["memories"]
        memory_id = added["memory"]["memory_id"]
        assert any(item["memory_id"] == memory_id for item in rows)
        await management_call("memories", "delete", memory_id=memory_id)
        assert all(
            item["memory_id"] != memory_id
            for item in (await management_call("memories", "list"))["memories"]
        )
        return monotonic() - began

    def snapshot(assist, management):
        files, vanished = _sample_files(config_dir)
        executor = getattr(hass.loop, "_default_executor", None)
        queue = getattr(executor, "_work_queue", None)
        item = {
            "rss_bytes": process.memory_info().rss,
            "descriptors": process.num_fds()
            if hasattr(process, "num_fds")
            else process.num_handles(),
            "threads": process.num_threads(),
            "storage_bytes": sum(size for _, size in files),
            "storage_files": len(files),
            "executor_pending": queue.qsize() if queue is not None else None,
            "loop_lag_max_seconds": max(lag_samples, default=0),
            "assist_p95_seconds": sorted(assist)[
                max(0, math_ceil(len(assist) * 0.95) - 1)
            ],
            "management_p95_seconds": sorted(management)[
                max(0, math_ceil(len(management) * 0.95) - 1)
            ],
            "assist_turns": len(assist),
            "management_operations": len(management),
            "sample_phase": "before_gc",
            "background_failures": list(background_failures),
            "transfer_files": sum(
                path.name.startswith("extended-openai-backup-") for path, _ in files
            ),
            "vanished_files_during_sample": vanished,
        }
        lag_samples.clear()
        return item

    from math import ceil as math_ceil

    probe = asyncio.create_task(responsiveness_probe())
    try:
        with (
            patch.object(helpers, "get_async_client", return_value=client),
            patch.object(agent_module, "async_track_time_interval", timed_retention),
            patch.object(
                config_flow,
                "get_authenticated_client",
                AsyncMock(return_value=object()),
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_NAME: "Process resource soak",
                    CONF_API_KEY: "sk-local-process-soak",
                    const.CONF_BASE_URL: url,
                    const.CONF_SKIP_AUTHENTICATION: True,
                    const.CONF_API_PROVIDER: "openai",
                },
            )
            assert result["type"] is FlowResultType.CREATE_ENTRY
            entry = result["result"]
            await hass.async_block_till_done()
            subentry = next(
                item
                for item in entry.subentries.values()
                if item.subentry_type == "conversation"
            )
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    **subentry.data,
                    const.CONF_API_MODE: const.API_MODE_CHAT_COMPLETIONS,
                    const.CONF_CHAT_MODEL: "gpt-5.6",
                    const.CONF_REASONING_EFFORT: "none",
                    const.CONF_MEMORY_MODE: const.MEMORY_MODE_MANUAL,
                    const.CONF_ARCHIVE_ENABLED: True,
                    const.CONF_KNOWLEDGE_ENABLED: True,
                    const.CONF_FUNCTION_TOOLS: [
                        {
                            "spec": {
                                "name": "lifetime_marker",
                                "description": "Lifetime native template",
                                "parameters": {"type": "object", "properties": {}},
                            },
                            "function": {
                                "type": "template",
                                "value_template": "LIFETIME_FUNCTION_HEALTHY",
                            },
                        }
                    ],
                },
            )
            await hass.async_block_till_done()
            _raw_client(
                conversation.async_get_agent(hass, entry.entry_id)
            ).max_retries = 0
            import yaml

            http_port = yaml.safe_load((config_dir / "configuration.yaml").read_text())[
                "http"
            ]["server_port"]
            refresh = await hass.auth.async_create_refresh_token(
                owner, "http://localhost"
            )
            websocket = await ws_session.ws_connect(
                f"http://127.0.0.1:{http_port}/api/websocket"
            )
            assert (await websocket.receive_json())["type"] == "auth_required"
            await websocket.send_json(
                {
                    "type": "auth",
                    "access_token": hass.auth.async_create_access_token(refresh),
                }
            )
            assert (await websocket.receive_json())["type"] == "auth_ok"
            from types import MappingProxyType

            primary = subentry
            alternate = ConfigSubentry(
                data=MappingProxyType(
                    {**primary.data, "api_mode": const.API_MODE_RESPONSES}
                ),
                subentry_type="conversation",
                title="Responses lifetime conversation",
                unique_id=None,
            )
            assert hass.config_entries.async_add_subentry(entry, alternate)
            await hass.async_block_till_done()
            conversation_subentries = [primary, alternate]
            source_ids = {}
            for subentry in conversation_subentries:
                # Retained records are never removed just to stabilise resource counts.
                await management_call(
                    "memories",
                    "add",
                    content="Retained lifetime preference",
                    category="preferences",
                    key="lifetime_retained",
                )
                source = await management_call(
                    "knowledge",
                    "create",
                    title="Lifetime source",
                    description="Ageing runtime",
                    content="Lifetime knowledge revision zero",
                )
                source_id = source["source"]["source_id"]
                rules_module = importlib.import_module(
                    f"custom_components.{DOMAIN}.request_rules"
                )
                rule_effects = []

                async def mark_rule(call, effects=rule_effects):
                    effects.append(dict(call.data))

                hass.services.async_register("lifetime_probe", "mark", mark_rule)
                await management_call(
                    "request_rules",
                    "create",
                    rule={
                        "id": "lifetime-rule",
                        "name": "Retained lifetime rule",
                        "enabled": True,
                        "phrases": ["Lifetime local rule"],
                        "match_type": "equals",
                        "action_type": "local_action",
                        "action": {
                            "actions": [
                                {
                                    "domain": "lifetime_probe",
                                    "service": "mark",
                                    "data": {"witness": "lifetime"},
                                }
                            ],
                            "success_response": "Lifetime local complete",
                            "failure_response": "Lifetime local failure",
                        },
                        "matching_behavior": "defaults",
                        "matching": dict(rules_module.DEFAULT_MATCHING),
                        "order": 0,
                    },
                )
                source_ids[subentry.subentry_id] = source_id
            subentry = primary
            task_subentry = next(
                item
                for item in entry.subentries.values()
                if item.subentry_type == "ai_task_data"
            )
            hass.config_entries.async_update_subentry(
                entry,
                task_subentry,
                data={
                    **task_subentry.data,
                    "chat_model": "gpt-5.6",
                    "reasoning_effort": "none",
                },
            )
            alternate_task = ConfigSubentry(
                data=MappingProxyType(
                    {**task_subentry.data, "api_mode": const.API_MODE_RESPONSES}
                ),
                subentry_type="ai_task_data",
                title="Responses lifetime task",
                unique_id=None,
            )
            assert hass.config_entries.async_add_subentry(entry, alternate_task)
            await hass.async_block_till_done()
            # Settle first-save normalization before the lifetime baseline. The
            # measured runtime thereafter performs only reviewed live fields.
            for candidate in conversation_subentries:
                subentry = candidate
                baseline_config = await management_call("configuration", "get")
                await management_call(
                    "configuration",
                    "update",
                    revision=baseline_config["revision"],
                    config={},
                )
                await hass.async_block_till_done()
            subentry = primary
            task_entities = {
                api: next(
                    row.entity_id
                    for row in er.async_get(hass).entities.values()
                    if row.config_entry_id == entry.entry_id
                    and row.domain == "ai_task"
                    and row.config_subentry_id == item.subentry_id
                )
                for api, item in (
                    (const.API_MODE_CHAT_COMPLETIONS, task_subentry),
                    (const.API_MODE_RESPONSES, alternate_task),
                )
            }
            agent_ids = {
                item.subentry_id: next(
                    row.entity_id
                    for row in er.async_get(hass).entities.values()
                    if row.config_entry_id == entry.entry_id
                    and row.domain == "conversation"
                    and row.config_subentry_id == item.subentry_id
                )
                for item in conversation_subentries
            }
            initial_agents = {
                key: conversation.async_get_agent(hass, value)
                for key, value in agent_ids.items()
            }
            active_agent_id = agent_ids[primary.subentry_id]
            from datetime import timedelta

            from homeassistant.util import dt as dt_util

            temporary_module = importlib.import_module(
                f"custom_components.{DOMAIN}.temporary_memory"
            )
            temporary = await temporary_module.async_get_temporary_memory(
                hass, entry.entry_id, subentry.subentry_id
            )
            expiry_owner = f"user:{owner.id}"
            await temporary.async_add(
                expiry_owner,
                "Naturally ageing lifetime fact",
                (dt_util.utcnow() + timedelta(seconds=2)).isoformat(),
                owner_scope_id=expiry_owner,
            )
            assert await temporary.async_active(
                expiry_owner, owner_scope_id=expiry_owner
            )
            number = 0
            abandoned = None
            if long_lifetime:
                assert transfers.TRANSFER_TTL_SECONDS == 900
                abandoned = await transfers._start_import(
                    hass,
                    entry.entry_id,
                    subentry.subentry_id,
                    {"filename": "idle-lifetime.zip", "size": 4096},
                )
                abandoned_id = abandoned["session_id"]
                abandoned_path = Path(transfers._imports(hass)[abandoned_id].path)
                assert abandoned_path.exists()
            for window in range(9 if long_lifetime else 5):
                assist, management = [], []
                subentry = conversation_subentries[window % 2]
                api = subentry.data["api_mode"]
                active_agent_id = agent_ids[subentry.subentry_id]
                source_id = source_ids[subentry.subentry_id]
                before = await management_call("configuration", "get")
                await management_call(
                    "configuration",
                    "update",
                    revision=before["revision"],
                    config={"max_tokens": 500 + window},
                )
                await hass.async_block_till_done()
                if uninterrupted:
                    assert (
                        conversation.async_get_agent(hass, active_agent_id)
                        is initial_agents[subentry.subentry_id]
                    )
                await management_call(
                    "knowledge",
                    "update",
                    source_id=source_id,
                    content=f"Lifetime knowledge revision {window}",
                )
                assert (await management_call("knowledge", "get", source_id=source_id))[
                    "source"
                ]["content"] == f"Lifetime knowledge revision {window}"
                feature_counts["knowledge"] += 1
                feature_counts["memory"] += 1
                feature_counts[api] += 1
                local, _ = await turn("Lifetime local rule", "lifetime-rule-session")
                assert (
                    _speech(local) == "Lifetime local complete"
                    and len(rule_effects) == window + 1
                )
                feature_counts["rules"] += 1
                function_before = feature_counts["functions"]
                tool_result, _ = await turn(
                    "Use lifetime function", f"lifetime-function-{window}"
                )
                assert _speech(tool_result) == "Process remains responsive."
                assert feature_counts["functions"] == function_before + 1
                generated = await ai_task.async_generate_data(
                    hass,
                    task_name=f"Lifetime task {window}",
                    entity_id=task_entities[api],
                    instructions="Return a healthy lifetime response",
                )
                assert generated.data == "Process remains responsive."
                feature_counts["ai_tasks"] += 1
                archive_module = importlib.import_module(
                    f"custom_components.{DOMAIN}.conversation_archive"
                )
                archive = await archive_module.async_get_archive(
                    hass, entry.entry_id, subentry.subentry_id
                )
                assert (await management_call("conversations", "list"))["sessions"]
                feature_counts["archive"] += 1
                await archive.async_prune(30)
                feature_counts["maintenance"] += 1
                duration = 10 if window == 0 else 225 if long_lifetime else 15 * scale
                began = monotonic()
                while monotonic() - began < duration:
                    results = await asyncio.gather(
                        *(
                            turn("Window conversation", f"soak-{index}")
                            for index in rng.sample(range(4), 2)
                        )
                    )
                    for result, latency in results:
                        assert _speech(result) == "Process remains responsive."
                        assist.append(latency)
                    management.append(await management_step(number))
                    number += 1
                    pause = (
                        min(45, max(0, duration - (monotonic() - began)))
                        if long_lifetime
                        else 0.15
                    )
                    idle_began = monotonic()
                    await asyncio.sleep(pause)
                    if long_lifetime:
                        idle_seconds += monotonic() - idle_began
                # Reload/failed transport/cancellation recovery exercise real client
                # and background lifetimes, rather than counting logical managers.
                if not uninterrupted and window in (
                    (1, 3, 5, 7) if long_lifetime else (1, 3)
                ):
                    assert await hass.config_entries.async_reload(entry.entry_id)
                    config_entry_reloads += 1
                    await hass.async_block_till_done()
                    _raw_client(
                        conversation.async_get_agent(hass, entry.entry_id)
                    ).max_retries = 0
                if window == 2:
                    failed, _ = await turn("Fail this transport")
                    assert failed.response.error_code is not None
                    held = asyncio.create_task(turn("Hold this stream"))
                    await asyncio.wait_for(held_started.wait(), 5)
                    held.cancel()
                    with suppress(asyncio.CancelledError):
                        await held
                    release_held.set()
                    recovered, _ = await turn("Recover after cancellation")
                    assert _speech(recovered) == "Process remains responsive."
                if long_lifetime and window == 5:
                    # Production TTL elapses naturally during idle. Cleanup is
                    # intentionally lazy: the next supported start reclaims it.
                    assert monotonic() - lifetime_started >= 900
                    assert abandoned_id in transfers._imports(hass)
                    fresh = await transfers._start_import(
                        hass,
                        entry.entry_id,
                        subentry.subentry_id,
                        {"filename": "healthy-lifetime.zip", "size": 4096},
                    )
                    assert abandoned_id not in transfers._imports(hass)
                    assert not abandoned_path.exists()
                    assert fresh["session_id"] in transfers._imports(hass)
                    await transfers._discard_import(hass, fresh["session_id"])
                    expired_transfer_reclaims += 1
                await hass.async_block_till_done()
                if window == 0:
                    assert not await temporary.async_active(
                        expiry_owner, owner_scope_id=expiry_owner
                    )
                    assert temporary.expired_pruned >= 1
                    feature_counts["expiry"] += 1
                item = await hass.async_add_executor_job(snapshot, assist, management)
                item["eoai_tasks"] = sum(
                    DOMAIN
                    in getattr(
                        getattr(task.get_coro(), "cr_code", None), "co_filename", ""
                    )
                    for task in asyncio.all_tasks()
                )
                item["subscriptions"] = sum(hass.bus.async_listeners().values())
                windows.append(item)
                # The gate consumes the uncollected sample. GC is diagnostic only.
                gc.collect()
                item["after_gc_diagnostic"] = {
                    "rss_bytes": process.memory_info().rss,
                    "threads": process.num_threads(),
                }
                (config_dir / _REPORT).write_text(
                    json.dumps(
                        {
                            "seed": seed,
                            "windows": windows,
                            "provider_requests": provider_requests,
                            "config_entry_reloads": config_entry_reloads,
                            "uninterrupted_runtime": uninterrupted,
                            "elapsed_seconds": monotonic() - lifetime_started,
                            "idle_seconds": idle_seconds,
                            "retention_callbacks": retention_callbacks,
                            "retention_harness_interval_seconds": 60
                            if long_lifetime
                            else 86400,
                            "expired_transfer_reclaims": expired_transfer_reclaims,
                            "feature_activity": feature_counts,
                            "management_transport": "authenticated_home_assistant_websocket",
                            "management_websocket_commands": ws_id,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            _assert_resource_windows(windows)
            final, _ = await turn("Final lifetime health request")
            assert _speech(final) == "Process remains responsive."
            if long_lifetime:
                assert monotonic() - lifetime_started >= 1800
                assert idle_seconds >= 1500
                assert retention_callbacks >= 20
                assert config_entry_reloads == (0 if uninterrupted else 4)
                assert expired_transfer_reclaims == 1
            report = json.loads((config_dir / _REPORT).read_text(encoding="utf-8"))
            _assert_populated_lifetime(report)
            report["final_healthy_requests"] = 1
            (config_dir / _REPORT).write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
    finally:
        lag_active = False
        await probe
        release_held.set()
        if websocket is not None:
            await websocket.close()
        await ws_session.close()
        await hass.async_stop()
        assert not background_failures, background_failures
        assert not transfers._exports(hass) and not transfers._imports(hass)
        assert not [
            path
            for path in config_dir.rglob("extended-openai-backup-*")
            if path.is_file()
        ]
        hass.loop.set_exception_handler(previous_exception_handler)
        logging.getLogger().removeHandler(failure_logs)
        await client.aclose()
        await server.cleanup()


def test_booted_process_resources_and_latency_survive_mixed_traffic(
    socket_enabled,
    tmp_path,
    unused_tcp_port,
    stress_seed,
    stress_scale,
    stress_trace,
):
    config_dir = tmp_path / "booted-resource-soak"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (config_dir / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Process Resource Soak\nrecorder:\nhttp:\n  server_host: 127.0.0.1\n  server_port: {unused_tcp_port}\n",
        encoding="utf-8",
    )
    result = run_python_child(
        __file__,
        cwd=config_dir,
        extra_env={
            _CHILD: "1",
            "EOAI_SOAK_SEED": str(stress_seed),
            "EOAI_SOAK_SCALE": str(stress_scale),
        },
        timeout=2400
        if os.environ.get("STRESS_CAMPAIGN") == "long-lifetime"
        else 180 + 90 * stress_scale,
    )
    report_path = config_dir / _REPORT
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    record(
        stress_trace,
        "summary",
        journey="booted_process_resource_soak",
        returncode=result.returncode,
        process_soak_windows=len(report.get("windows", [])),
        pre_gc_resource_samples=len(report.get("windows", [])),
        genuine_management_websocket_commands=report.get(
            "management_websocket_commands", 0
        ),
        lifetime_elapsed_seconds=int(report.get("elapsed_seconds", 0)),
        lifetime_idle_seconds=int(report.get("idle_seconds", 0)),
        lifetime_retention_callbacks=report.get("retention_callbacks", 0),
        lifetime_expired_transfer_reclaims=report.get("expired_transfer_reclaims", 0),
        lifetime_reloads=report.get("config_entry_reloads", 0),
        lifetime_final_healthy_requests=report.get("final_healthy_requests", 0),
        metrics=report,
        stdout=result.stdout[-2000:],
        stderr=result.stderr[-4000:],
    )
    assert result.returncode == 0, (
        f"booted process soak failed\n{result.stdout}\n{result.stderr}"
    )
    _assert_populated_lifetime(report)
    _assert_resource_windows(report["windows"])


if __name__ == "__main__" and os.environ.get(_CHILD) == "1":
    asyncio.run(
        _booted_soak(
            Path.cwd(),
            int(os.environ["EOAI_SOAK_SEED"]),
            int(os.environ["EOAI_SOAK_SCALE"]),
        )
    )


@pytest.mark.skipif(
    os.environ.get("STRESS_CAMPAIGN") != "long-lifetime",
    reason="uninterrupted companion is long-lifetime nightly only",
)
def test_booted_process_uninterrupted_runtime_survives_populated_lifetime(
    socket_enabled,
    tmp_path,
    unused_tcp_port,
    stress_seed,
    stress_scale,
    stress_trace,
):
    """Companion soak keeps one runtime identity with no config-entry reloads."""
    config_dir = tmp_path / "booted-resource-soak-uninterrupted"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (config_dir / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Process Resource Soak Uninterrupted\n"
        f"recorder:\nhttp:\n  server_host: 127.0.0.1\n"
        f"  server_port: {unused_tcp_port}\n",
        encoding="utf-8",
    )
    result = run_python_child(
        __file__,
        cwd=config_dir,
        extra_env={
            _CHILD: "1",
            "EOAI_SOAK_SEED": str(stress_seed ^ 0x51504B),
            "EOAI_SOAK_SCALE": str(stress_scale),
            "EOAI_UNINTERRUPTED_SOAK": "1",
        },
        timeout=2400,
    )
    report_path = config_dir / _REPORT
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    record(
        stress_trace,
        "summary",
        journey="booted_process_uninterrupted_soak",
        uninterrupted_lifetime_windows=len(report.get("windows", [])),
        populated_ageing_features=sum(
            bool(report.get("feature_activity", {}).get(name))
            for name in _LIFETIME_FEATURES
        ),
        pre_gc_resource_samples=len(report.get("windows", [])),
        genuine_management_websocket_commands=report.get(
            "management_websocket_commands", 0
        ),
        uninterrupted_lifetime_reloads=report.get("config_entry_reloads", -1),
        uninterrupted_lifetime_final_healthy_requests=report.get(
            "final_healthy_requests", 0
        ),
        metrics=report,
    )
    assert result.returncode == 0, (
        f"uninterrupted booted process soak failed\n{result.stdout}\n{result.stderr}"
    )
    assert report.get("uninterrupted_runtime") is True
    _assert_populated_lifetime(report)
    assert report.get("config_entry_reloads") == 0
    assert report.get("final_healthy_requests") == 1
    _assert_resource_windows(report["windows"])
