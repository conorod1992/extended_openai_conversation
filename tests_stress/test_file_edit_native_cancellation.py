"""File edit ownership at the real surviving HA executor boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from custom_components.extended_openai_conversation_responses.functions import file
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from tests.lock_probe import LockProbe
from tests_stress.conftest import record


async def test_cancelled_native_edit_retains_path_until_worker_settles(
    hass: HomeAssistant, tmp_path: Path, monkeypatch, stress_trace: list[dict]
) -> None:
    target = tmp_path / "owned.txt"
    target.write_text("first second", encoding="utf-8")
    entered = asyncio.Event()
    settled = asyncio.Event()
    release = threading.Event()
    native = file._atomic_replace_text
    loop = asyncio.get_running_loop()

    def gated_native(path, content):
        # Called by the real unchanged-fingerprint worker after its check.
        if content == "FIRST second":
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(20), "native gate was never released"
                return native(path, content)
            finally:
                loop.call_soon_threadsafe(settled.set)
        return native(path, content)

    monkeypatch.setattr(file, "_atomic_replace_text", gated_native)
    config = {
        "path": Template(str(target), hass),
        "old_text": Template("{{ old }}", hass),
        "new_text": Template("{{ new }}", hass),
        "allow_dir": [Template(str(tmp_path), hass)],
    }

    async def edit(old, new):
        return await file.EditFileFunction().execute(
            hass, config, {"old": old, "new": new}, None, []
        )

    first = asyncio.create_task(edit("first", "FIRST"))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert not settled.is_set()
        first.cancel()
        await asyncio.sleep(0)  # deliver cancellation, never a native timing gate
        record(stress_trace, "native_edit_cancelled", worker_alive=not settled.is_set())
        second = asyncio.create_task(edit("second", "SECOND"))
        if first.done():
            # On the unfixed implementation B can acknowledge before A resumes.
            result = await asyncio.wait_for(second, 10)
            assert result["success"]
            assert target.read_text() == "first SECOND"
            record(stress_trace, "later_edit_acknowledged_before_native_settlement")
        else:
            await asyncio.sleep(0)
            assert file._get_edit_lock(hass, target.resolve()).locked()
            assert not second.done()
            # Repeated cancellation must not abandon the worker either.
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done() and not settled.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        result = await asyncio.wait_for(second, 10)
        assert result["success"]
        await asyncio.wait_for(settled.wait(), 10)
        assert target.read_text() == "FIRST SECOND", "stale native writer overwrote B"
        assert list(tmp_path.glob(".owned.txt.*.tmp")) == []
        assert (await edit("FIRST", "RECOVERED"))["success"]
        assert target.read_text() == "RECOVERED SECOND"
        record(
            stress_trace,
            "native_edit_recovered",
            final=target.read_text(),
            temp_files=0,
        )
        record(
            stress_trace, "summary", native_edit_settlement_cases=1, layer="genuine-ha"
        )
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )


@pytest.mark.parametrize(
    "older,newer", [("write", "write"), ("write", "edit"), ("edit", "write")]
)
async def test_cancelled_native_writer_retains_path_across_write_and_edit(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch,
    stress_trace: list[dict],
    older,
    newer,
) -> None:
    target = tmp_path / "owned.txt"
    target.write_text("first second", encoding="utf-8")
    entered, settled = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    native = file._atomic_replace_text
    loop = asyncio.get_running_loop()
    lock = file._get_edit_lock(hass, target.resolve())
    probe = LockProbe(lock)
    monkeypatch.setattr(file, "_get_edit_lock", lambda _hass, path: probe)

    def gated_native(path, content):
        if content == "FIRST second":
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(20), "native gate was never released"
                return native(path, content)
            finally:
                loop.call_soon_threadsafe(settled.set)
        return native(path, content)

    monkeypatch.setattr(file, "_atomic_replace_text", gated_native)

    async def mutate(kind, *, first):
        config = {
            "path": Template(str(target), hass),
            "allow_dir": [Template(str(tmp_path), hass)],
        }
        if kind == "write":
            config["content"] = Template(
                "FIRST second" if first else "ACKNOWLEDGED", hass
            )
            function = file.WriteFileFunction()
        else:
            config.update(
                old_text=Template("first" if first else "second", hass),
                new_text=Template("FIRST" if first else "SECOND", hass),
            )
            function = file.EditFileFunction()
        return await function.execute(hass, config, {}, None, [])

    first = asyncio.create_task(mutate(older, first=True))
    second = None
    try:
        assert await probe.next_attempt() is first
        await asyncio.wait_for(entered.wait(), 10)
        first.cancel()
        second = asyncio.create_task(mutate(newer, first=False))
        assert await probe.next_attempt() is second
        assert lock.locked() and not settled.is_set()
        assert not first.done() and not second.done()
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await asyncio.wait_for(second, 10))["success"]
        await asyncio.wait_for(settled.wait(), 10)
        await hass.async_block_till_done()
        expected = "ACKNOWLEDGED" if newer == "write" else "FIRST SECOND"
        assert target.read_text() == expected
        assert list(tmp_path.glob(".owned.txt.*.tmp")) == []
        assert (await mutate("write", first=False))["success"]
        assert target.read_text() == "ACKNOWLEDGED"
        record(
            stress_trace,
            "summary",
            native_file_settlement_cases=1,
            older=older,
            newer=newer,
            layer="genuine-ha",
        )
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )
