"""Real download service cancellation owns only its staging and surviving workers."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import services
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_DOWNLOAD_SKILL,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    ReadFileFunction,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from tests_stress.conftest import record
from tests_stress.test_skill_lifecycle_campaign import _write_skill


@pytest.mark.parametrize(
    "phase", ["prepare", "listing-response", "file-response", "write", "collision"]
)
async def test_cancelled_download_settles_native_work_before_owned_cleanup(
    hass, tmp_path, monkeypatch, stress_trace, phase
):
    monkeypatch.setattr(SkillManager, "_instance", None)
    root = tmp_path / "skills"
    _write_skill(root / "demo", "OLD installed")
    manager = await SkillManager.async_get_instance(hass, str(root))
    foreign = manager.staging_dir / "demo.download-foreign"
    foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_bytes(b"unowned evidence")
    staged = manager.staging_dir / "demo.download-owned"
    if phase == "collision":
        staged.mkdir()
        (staged / "keep.txt").write_bytes(b"colliding unowned evidence")
    monkeypatch.setattr(services, "uuid4", lambda: SimpleNamespace(hex="owned"))
    monkeypatch.setattr(
        services, "async_skill_source_ref", AsyncMock(return_value="reviewed-ref")
    )
    entered, settled, stream_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    native_release = threading.Event()
    loop = asyncio.get_running_loop()
    executor = hass.async_add_executor_job
    cleanup_calls = []
    chosen = {"prepare": "_prepare_staging", "write": "_write_file_sync"}.get(phase)
    gate_used = False

    def add_executor(function, *args):
        nonlocal gate_used
        if function.__name__ == chosen and not gate_used:
            gate_used = True

            def gated(*native_args):
                try:
                    result = function(*native_args)
                    loop.call_soon_threadsafe(entered.set)
                    assert native_release.wait(20), (
                        "native download gate was never released"
                    )
                    return result
                finally:
                    loop.call_soon_threadsafe(settled.set)

            return executor(gated, *args)
        if function.__name__ == "_cleanup_path":
            assert not chosen or settled.is_set(), "cleanup raced surviving native work"
            cleanup_calls.append(args[0])
        return executor(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", add_executor)
    body = b"---\ndescription: NEW installed\n---\nNew instructions.\n"
    listing = json.dumps(
        [
            {
                "name": "SKILL.md",
                "type": "file",
                "download_url": "https://download.example/SKILL.md",
                "path": "skills/demo/SKILL.md",
                "size": len(body),
            }
        ]
    ).encode()
    responses = []
    response_gate_used = False

    class Response:
        status = 200
        content_length = None

        def __init__(self, payload, gate):
            self.payload, self.gate, self.closed = payload, gate, False
            self.content = self
            responses.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def iter_chunked(self, _size):
            yield self.payload[:1]
            if self.gate:
                entered.set()
                await stream_release.wait()
            yield self.payload[1:]

    def get(url):
        nonlocal response_gate_used
        is_file = url == "https://download.example/SKILL.md"
        gate = not response_gate_used and phase == (
            "file-response" if is_file else "listing-response"
        )
        response_gate_used |= gate
        return Response(body if is_file else listing, gate)

    monkeypatch.setattr(
        services, "async_get_clientsession", lambda _hass: SimpleNamespace(get=get)
    )
    await services.async_setup_services(hass, {})
    admin = await hass.auth.async_create_user(
        "Download admin", group_ids=["system-admin"]
    )

    async def download():
        return await hass.services.async_call(
            DOMAIN,
            SERVICE_DOWNLOAD_SKILL,
            {"skill_name": "demo", "source_ref": "reviewed-ref"},
            blocking=True,
            return_response=True,
            context=Context(user_id=admin.id),
        )

    async def assert_old_usable():
        assert manager.get_skill("demo").description == "OLD installed"
        result = await ReadFileFunction().execute(
            hass,
            {"path": Template("{{ extended_openai.skill_dir }}/SKILL.md", hass)},
            {"name": "demo", "extended_openai": {"skill_dir": str(root / "demo")}},
            None,
            [],
        )
        assert "OLD installed" in result["content"]

    task = asyncio.create_task(download())
    try:
        if phase == "collision":
            with pytest.raises(HomeAssistantError):
                await task
            assert (staged / "keep.txt").read_bytes() == b"colliding unowned evidence"
            assert cleanup_calls == []
            # Repair the collision externally; the service never owns this tree.
            monkeypatch.setattr(services, "uuid4", lambda: SimpleNamespace(hex="retry"))
        else:
            await asyncio.wait_for(entered.wait(), 10)
            assert staged.exists() and not task.done()
            await assert_old_usable()
            task.cancel()
            if chosen:
                await asyncio.sleep(0)
                assert not task.done() and not settled.is_set() and not cleanup_calls
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            native_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            if chosen:
                await asyncio.wait_for(settled.wait(), 5)
            await hass.async_block_till_done()
            assert not staged.exists()
            assert cleanup_calls == [staged]
            assert all(response.closed for response in responses)
        await assert_old_usable()
        assert (foreign / "keep.txt").read_bytes() == b"unowned evidence"
        stream_release.set()
        result = await download()
        assert result["downloaded_files"] == ["skills/demo/SKILL.md"]
        assert manager.get_skill("demo").description == "NEW installed"
        monkeypatch.setattr(SkillManager, "_instance", None)
        fresh = await SkillManager.async_get_instance(hass, str(root))
        assert fresh is not manager
        assert fresh.get_skill("demo").description == "NEW installed"
        assert (foreign / "keep.txt").read_bytes() == b"unowned evidence"
        assert list(manager.staging_dir.glob("demo.download-retry")) == []
        if phase != "collision":
            assert not staged.exists()
        record(
            stress_trace,
            "summary",
            skill_download_cancellation_cases=int(phase != "collision"),
            skill_download_unowned_cases=1,
            phase=phase,
            layer="genuine-ha-service-executor",
        )
    finally:
        native_release.set()
        stream_release.set()
        await asyncio.gather(task, return_exceptions=True)
