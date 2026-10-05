"""Genuine Home Assistant whole-config backup/restore acceptance for EOAI."""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any, ClassVar

import yaml

from tests_real_ha.process_harness import child_process_env

DOMAIN = "extended_openai_conversation_responses"
_PHASE = "EOAI_NATIVE_BACKUP_PHASE"
_CONFIG = "EOAI_NATIVE_BACKUP_CONFIG"
_ENDPOINT = "EOAI_NATIVE_BACKUP_ENDPOINT"
_INFO = "native-backup-info.json"


class _Provider(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def log_message(self, *args):
        del args

    def _reply(self, status, value, content_type="application/json"):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/v1/models":
            return self._reply(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "gpt-5.6",
                            "object": "model",
                            "created": 0,
                            "owned_by": "native-backup-acceptance",
                        }
                    ],
                },
            )
        return self._reply(404, {})

    def do_POST(self):
        type(self).requests.append(self.path)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        marker = (
            "Restored installation healthy"
            if "restored installation" in json.dumps(body).lower()
            else "Native backup source healthy"
        )
        chunk = {
            "id": "native-backup",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": marker},
                    "finish_reason": "stop",
                }
            ],
        }
        if self.path == "/v1/chat/completions":
            return self._reply(
                200,
                "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                "text/event-stream",
            )
        return self._reply(404, {})


def _tool_config() -> dict[str, Any]:
    return {
        "spec": {
            "name": "native_backup_marker",
            "description": "Return the native backup acceptance marker.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "BACKUP_TOOL_RESTORED"},
        "enabled": True,
    }


async def _create_entry(hass, endpoint):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Native HA backup acceptance",
            CONF_API_KEY: "sk-native-backup",
            const.CONF_BASE_URL: endpoint + "/v1",
            const.CONF_SKIP_AUTHENTICATION: False,
            const.CONF_API_PROVIDER: "openai",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def _say(hass, entry_id, text):
    from homeassistant.components import conversation
    from homeassistant.core import Context

    result = await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )
    assert result.response.error_code is None, result.response.as_dict()
    return result.response.as_dict()["speech"]["plain"]["speech"]


async def _seed(config_dir: Path, endpoint: str) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.components.backup.const import DATA_MANAGER
    from homeassistant.setup import async_setup_component

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()
    try:
        assert await async_setup_component(hass, "backup", {})
        entry = await _create_entry(hass, endpoint)
        subentry = next(
            item for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        )
        const = importlib.import_module(f"custom_components.{DOMAIN}.const")
        updated = dict(subentry.data)
        updated[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump(
            [_tool_config()], sort_keys=False, allow_unicode=True
        )
        hass.config_entries.async_update_subentry(entry, subentry, data=updated)
        await hass.async_block_till_done()

        from custom_components.extended_openai_conversation_responses.knowledge import (
            async_get_knowledge,
        )
        from custom_components.extended_openai_conversation_responses.memory import (
            async_get_memory,
        )

        memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
        knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
        await memory.async_add(
            "user:native-backup",
            "NATIVE BACKUP PRIVATE MEMORY",
            "acceptance",
            "explicit",
        )
        source = await knowledge.async_create(
            "Native backup source",
            "acceptance",
            "NATIVE BACKUP KNOWLEDGE",
        )
        assert await _say(hass, entry.entry_id, "source installation") == (
            "Native backup source healthy"
        )

        # Flush config-entry state before asking HA itself to snapshot /config.
        await hass.config_entries._store.async_save(
            hass.config_entries._data_to_save()
        )
        await hass.async_block_till_done()

        manager = hass.data[DATA_MANAGER]
        created = await manager.async_create_backup(
            agent_ids=["backup.local"],
            include_addons=None,
            include_all_addons=False,
            include_database=True,
            include_folders=None,
            include_homeassistant=True,
            name="EOAI native whole-installation acceptance",
            password=None,
        )
        backup_id = created.backup_job_id
        local = manager.local_backup_agents["backup.local"]
        backup_path = local.get_backup_path(backup_id)
        assert backup_path.is_file() and backup_path.stat().st_size > 0

        # This file is deliberately created after the native snapshot. Its absence
        # after restore proves the test did not simply copy the source config tree.
        (config_dir / "post-backup-not-restored").write_text(
            "must not survive", encoding="utf-8"
        )
        (config_dir / _INFO).write_text(
            json.dumps(
                {
                    "backup_id": backup_id,
                    "backup_path": str(backup_path),
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry.subentry_id,
                    "knowledge_id": source.source_id,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        await hass.async_stop()


def _restore(config_dir: Path) -> None:
    from homeassistant.backup_restore import restore_backup

    assert restore_backup(str(config_dir))
    result = json.loads((config_dir / ".HA_RESTORE_RESULT").read_text())
    assert result == {"success": True, "error": None, "error_type": None}


async def _verify(config_dir: Path) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState

    sys.path.insert(0, str(config_dir))
    assert not (config_dir / "post-backup-not-restored").exists()
    info = json.loads((config_dir / "restored-expected.json").read_text())
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        entry = entries[0]
        await hass.async_block_till_done()
        assert entry.entry_id == info["entry_id"]
        assert entry.state is ConfigEntryState.LOADED
        subentry = next(
            item for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        )
        assert subentry.subentry_id == info["subentry_id"]

        from custom_components.extended_openai_conversation_responses.agent_config import (
            configured_function_tools_from_data,
        )
        from custom_components.extended_openai_conversation_responses.knowledge import (
            async_get_knowledge,
        )
        from custom_components.extended_openai_conversation_responses.memory import (
            async_get_memory,
        )

        tools = configured_function_tools_from_data(subentry.data)
        assert any(
            item["spec"]["name"] == "native_backup_marker"
            and item["function"]["value_template"] == "BACKUP_TOOL_RESTORED"
            for item in tools
        )
        memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
        assert any(
            item.content == "NATIVE BACKUP PRIVATE MEMORY"
            for item in await memory.async_list("user:native-backup")
        )
        knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
        source = await knowledge.async_get(info["knowledge_id"])
        assert source is not None and source.content == "NATIVE BACKUP KNOWLEDGE"
        assert [
            item.source_id for item in await knowledge.async_search(
                "NATIVE BACKUP KNOWLEDGE"
            )
        ] == [info["knowledge_id"]]
        assert conversation.async_get_agent(hass, entry.entry_id) is not None
        assert await _say(hass, entry.entry_id, "restored installation") == (
            "Restored installation healthy"
        )
    finally:
        await hass.async_stop()


async def _child_main() -> None:
    config = Path(os.environ[_CONFIG]).resolve()
    phase = os.environ[_PHASE]
    endpoint = os.environ.get(_ENDPOINT, "")
    if phase == "seed":
        await _seed(config, endpoint)
    elif phase == "restore":
        _restore(config)
    elif phase == "verify":
        await _verify(config)
    else:
        raise AssertionError(f"Unknown native backup phase: {phase}")


def _run(config: Path, phase: str, endpoint: str = ""):
    env = child_process_env(
        __file__,
        {_PHASE: phase, _CONFIG: str(config), _ENDPOINT: endpoint},
        working_directory=config,
    )
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def test_native_home_assistant_backup_restores_populated_eoai_to_fresh_installation(
    tmp_path: Path,
) -> None:
    """Create with HA's backup manager and restore through HA's startup restorer."""
    source_component = (
        Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    )
    source_config = tmp_path / "source-config"
    destination = source_config / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source_component, destination)
    (source_config / "configuration.yaml").write_text(
        "homeassistant:\n  name: Native backup source\nrecorder:\n",
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        seeded = _run(source_config, "seed", endpoint)
        assert seeded.returncode == 0, (
            "native backup seed failed\n"
            f"stdout:\n{seeded.stdout}\n\nstderr:\n{seeded.stderr}"
        )
        info = json.loads((source_config / _INFO).read_text())
        backup = Path(info["backup_path"])
        assert backup.exists()

        restored_config = tmp_path / "restored-config"
        restored_config.mkdir()
        copied_backup = restored_config / "native-ha-backup.tar"
        shutil.copy2(backup, copied_backup)
        (restored_config / ".HA_RESTORE").write_text(
            json.dumps(
                {
                    "path": str(copied_backup),
                    "password": None,
                    "remove_after_restore": False,
                    "restore_database": True,
                    "restore_homeassistant": True,
                }
            ),
            encoding="utf-8",
        )
        (restored_config / "restored-expected.json").write_text(
            json.dumps(info), encoding="utf-8"
        )

        restored = _run(restored_config, "restore")
        assert restored.returncode == 0, (
            "native HA restore failed\n"
            f"stdout:\n{restored.stdout}\n\nstderr:\n{restored.stderr}"
        )
        # The expected IDs are test evidence, not application state. Recreate the
        # observer file after HA has restored the clean point-in-time snapshot.
        (restored_config / "restored-expected.json").write_text(
            json.dumps(info), encoding="utf-8"
        )
        verified = _run(restored_config, "verify", endpoint)
        assert verified.returncode == 0, (
            "cold boot of native-restored HA failed\n"
            f"stdout:\n{verified.stdout}\n\nstderr:\n{verified.stderr}"
        )
        assert "/v1/models" in _Provider.requests
        assert "/v1/chat/completions" in _Provider.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__" and os.environ.get(_PHASE):
    asyncio.run(_child_main())
