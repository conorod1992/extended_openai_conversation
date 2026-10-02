"""Booted-process resource windows over actual fragmented provider traffic."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import gc
import importlib
from itertools import pairwise
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import sys
from time import monotonic
from unittest.mock import AsyncMock, patch

import pytest

from tests_real_ha.process_harness import run_python_child
from tests_real_ha.test_packaged_process_restart import DOMAIN, _assert_packaged_module
from tests_stress.conftest import record

_CHILD = "EOAI_RESOURCE_SOAK_CHILD"
_REPORT = "process-resource-windows.json"


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
        }[key]
        assert not increments or not all(delta > material for delta in increments), (
            key,
            windows,
        )
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
        "loop_lag_max_seconds": 0.01,
        "assist_p95_seconds": 0.1,
        "management_p95_seconds": 0.01,
        "assist_turns": 4,
        "management_operations": 4,
    }
    windows = [dict(baseline) for _ in range(5)]
    if failure in {"rss_bytes", "descriptors", "storage_bytes", "executor_pending"}:
        growth = {
            "rss_bytes": 80_000_000,
            "descriptors": 20,
            "storage_bytes": 40_000_000,
            "executor_pending": 20,
        }[failure]
        for index, item in enumerate(windows[1:], 1):
            item[failure] += growth * index
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
    from aiohttp import web
    import httpx
    import psutil

    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.core import Context
    from homeassistant.data_entry_flow import FlowResultType

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
        _raw_client,
        _speech,
    )

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    helpers = importlib.import_module(f"custom_components.{DOMAIN}.helpers")
    agent_module = importlib.import_module(f"custom_components.{DOMAIN}.conversation")
    transfers = importlib.import_module(f"custom_components.{DOMAIN}.backup_transfer")
    long_lifetime = os.environ.get("STRESS_CAMPAIGN") == "long-lifetime"
    lifetime_started = monotonic()
    retention_callbacks = 0
    idle_seconds = 0.0
    expired_transfer_reclaims = 0
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
        content = str(body["messages"][-1].get("content", ""))
        if content == "Fail this transport":
            assert request.transport is not None
            request.transport.abort()
            return web.Response()
        payload = _chat_sse_text("Process remains responsive.")
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
                agent_id=entry.entry_id,
            ),
            20,
        )
        return result, monotonic() - began

    async def management_step(number):
        began = monotonic()
        agent = conversation.async_get_agent(hass, entry.entry_id)
        added = await agent._memory.async_add(
            owner.id,
            f"Soak preference {number}",
            "preferences",
            "explicit",
            key=f"soak_{number}",
        )
        rows = await agent._memory.async_list(owner.id)
        memory_id = added["memory"]["memory_id"]
        assert any(item.memory_id == memory_id for item in rows)
        assert await agent._memory.async_delete(owner.id, [memory_id]) == 1
        assert await agent._memory.async_list(owner.id) == []
        return monotonic() - began

    def snapshot(assist, management):
        files = [path for path in config_dir.rglob("*") if path.is_file()]
        executor = getattr(hass.loop, "_default_executor", None)
        queue = getattr(executor, "_work_queue", None)
        item = {
            "rss_bytes": process.memory_info().rss,
            "descriptors": process.num_fds()
            if hasattr(process, "num_fds")
            else process.num_handles(),
            "threads": process.num_threads(),
            "storage_bytes": sum(path.stat().st_size for path in files),
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
                    const.CONF_ARCHIVE_ENABLED: long_lifetime,
                },
            )
            await hass.async_block_till_done()
            _raw_client(
                conversation.async_get_agent(hass, entry.entry_id)
            ).max_retries = 0
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
                if window in ((1, 3, 5, 7) if long_lifetime else (1, 3)):
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
                gc.collect()
                await asyncio.sleep(0.1)
                windows.append(
                    await hass.async_add_executor_job(snapshot, assist, management)
                )
                (config_dir / _REPORT).write_text(
                    json.dumps(
                        {
                            "seed": seed,
                            "windows": windows,
                            "provider_requests": provider_requests,
                            "config_entry_reloads": config_entry_reloads,
                            "elapsed_seconds": monotonic() - lifetime_started,
                            "idle_seconds": idle_seconds,
                            "retention_callbacks": retention_callbacks,
                            "retention_harness_interval_seconds": 60
                            if long_lifetime
                            else 86400,
                            "expired_transfer_reclaims": expired_transfer_reclaims,
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
                assert config_entry_reloads == 4
                assert expired_transfer_reclaims == 1
            report = json.loads((config_dir / _REPORT).read_text(encoding="utf-8"))
            report["final_healthy_requests"] = 1
            (config_dir / _REPORT).write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
    finally:
        lag_active = False
        await probe
        release_held.set()
        await hass.async_stop()
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
    _assert_resource_windows(report["windows"])


if __name__ == "__main__" and os.environ.get(_CHILD) == "1":
    asyncio.run(
        _booted_soak(
            Path.cwd(),
            int(os.environ["EOAI_SOAK_SEED"]),
            int(os.environ["EOAI_SOAK_SCALE"]),
        )
    )
