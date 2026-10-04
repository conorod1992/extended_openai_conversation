"""Booted HA process kills at transfer and Skill filesystem boundaries."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from tests_real_ha.process_harness import child_process_env, run_python_child
from tests_real_ha.test_immediate_tool_process_crash import (
    DOMAIN,
    _assert_source_component,
    _ensure_entry,
    _stage_component,
)

_PHASE = "FILESYSTEM_RECOVERY_PHASE"
_CONFIG = "FILESYSTEM_RECOVERY_CONFIG"
_KIND = "FILESYSTEM_RECOVERY_KIND"
_ACTIVE_PAYLOAD = "FILESYSTEM_RECOVERY_ACTIVE_PAYLOAD"


def _env(config, phase, kind):
    return {
        _PHASE: phase,
        _CONFIG: str(config),
        _KIND: kind,
        "TMPDIR": str(config / "preserved-temp"),
    }


def _payload_files(config):
    return sorted(
        path
        for path in config.rglob("extended-openai-backup-*")
        if path.is_file() and str(path) != os.environ.get(_ACTIVE_PAYLOAD)
    )


async def _child(config: Path, phase: str, kind: str):
    from custom_components.extended_openai_conversation_responses import (
        backup_transfer as transfers,
    )
    from custom_components.extended_openai_conversation_responses.skills import (
        SkillManager,
    )
    from homeassistant import bootstrap, runner
    from tests_stress.test_skill_lifecycle_campaign import _write_skill

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()
    entry = await _ensure_entry(hass)
    subentry = next(
        s for s in entry.subentries.values() if s.subentry_type == "conversation"
    )

    async def command(action, data=None):
        return await transfers.async_backup_transfer_command(
            hass,
            {
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "action": action,
                "data": data or {},
            },
        )

    manager = await SkillManager.async_get_instance(hass)
    target = manager.user_skills_dir / "owned-skill"
    if kind == "transfer":
        previous = (
            json.loads((config / "cut.json").read_text())
            if (config / "cut.json").exists()
            else None
        )
        if phase == "verify":
            assert previous is not None
            assert not transfers._imports(hass) and not transfers._exports(hass)
            assert transfers._temporary_bytes(hass) == 0
            assert subentry.title == "LIVE TRANSFER TARGET"
            if os.environ.get(_ACTIVE_PAYLOAD):
                assert (
                    Path(os.environ[_ACTIVE_PAYLOAD]).read_bytes()
                    == b"OTHER-LIVE-OWNER"
                )
            assert _payload_files(config) == [], (
                "unaccounted abandoned payload survived restart"
            )
            assert (
                config / "preserved-temp" / "unrelated-sentinel"
            ).read_text() == "KEEP"
            for action, key in [
                ("import_cancel", "import_id"),
                ("export_cancel", "export_id"),
            ]:
                result = await command(action, {"session_id": previous[key]})
                assert not result["cancelled"]
        hass.config_entries.async_update_subentry(
            entry, subentry, title="ABANDONED IMPORT CANDIDATE"
        )
        await hass.async_block_till_done()
        exported = await command("export_start")
        assert exported["chunk_count"] == 1
        hass.config_entries.async_update_subentry(
            entry, subentry, title="LIVE TRANSFER TARGET"
        )
        await hass.async_block_till_done()
        await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
        durable = json.loads(Path(hass.config_entries._store.path).read_text())
        durable_entry = next(
            item
            for item in durable["data"]["entries"]
            if item["entry_id"] == entry.entry_id
        )
        durable_subentry = next(
            item
            for item in durable_entry["subentries"]
            if item["subentry_id"] == subentry.subentry_id
        )
        assert durable_subentry["title"] == "LIVE TRANSFER TARGET"
        upload = await command(
            "import_start", {"filename": "probe.zip", "size": exported["size"]}
        )
        chunk = await command(
            "export_chunk", {"session_id": exported["session_id"], "index": 0}
        )
        assert chunk["data"]
        await command(
            "import_chunk",
            {"session_id": upload["session_id"], "index": 0, "data": chunk["data"]},
        )
        files = _payload_files(config)
        assert len(files) == 2
        assert sum(path.stat().st_size for path in files) > 0
        if phase == "crash":
            (config / "cut.json").write_text(
                json.dumps(
                    {
                        "files": [
                            {"path": str(p), "size": p.stat().st_size} for p in files
                        ],
                        "import_id": upload["session_id"],
                        "export_id": exported["session_id"],
                    }
                )
            )
            (config / "paused").write_text("real upload chunk and staged export")
            await asyncio.Event().wait()
        else:
            assert base64.b64decode(chunk["data"]).startswith(b"PK")
            inspected = await command(
                "import_inspect", {"session_id": upload["session_id"]}
            )
            restored = await command(
                "import_restore",
                {
                    "session_id": upload["session_id"],
                    "preview_token": inspected["preview_token"],
                },
            )
            assert restored["status"] == "restored"
            await hass.async_block_till_done()
            assert subentry.title == "ABANDONED IMPORT CANDIDATE"
            await command("export_cancel", {"session_id": exported["session_id"]})
            assert (
                _payload_files(config) == [] and transfers._temporary_bytes(hass) == 0
            )
            (config / "verify.json").write_text(
                json.dumps(
                    {
                        "orphan_payload_bytes": 0,
                        "accounted_bytes": 0,
                        "abandoned_import_not_applied": True,
                        "ordinary_import_restored": True,
                        "active_owner_preserved": bool(os.environ.get(_ACTIVE_PAYLOAD)),
                    }
                )
            )
    else:
        if phase == "seed":
            _write_skill(target, "Previously known good metadata")
            (target / "SKILL.md").write_text(
                "---\ndescription: Previously known good metadata\n---\nOLD-KNOWN-GOOD-BODY\n"
            )
            await manager.async_load_skills()
            assert manager.get_skill("owned-skill") is not None
        elif phase == "crash":
            staged = manager.staging_dir / "owned-candidate"
            if kind == "publish":
                _write_skill(staged, "Replacement metadata")
                (staged / "SKILL.md").write_text(
                    "---\ndescription: Replacement metadata\n---\nNEW-CANDIDATE-BODY\n"
                )
            rename = Path.rename

            def cut_rename(path, destination):
                result = rename(path, destination)
                if path == target:
                    backup = Path(destination)
                    assert (
                        not target.exists()
                        and "OLD-KNOWN-GOOD-BODY" in (backup / "SKILL.md").read_text()
                    )
                    (config / "cut.json").write_text(
                        json.dumps(
                            {"backup": str(backup), "staged": str(staged), "kind": kind}
                        )
                    )
                    (config / "paused").write_text("first real rename completed")
                    threading.Event().wait()
                return result

            Path.rename = cut_rename
            if kind == "publish":
                await manager.async_publish_staged_skill("owned-skill", staged)
            else:
                await manager.async_remove_skill("owned-skill")
            raise AssertionError("operation escaped process-kill gate")
        else:
            skill = manager.get_skill("owned-skill")
            assert skill is not None, (
                "recoverable known-good Skill silently disappeared"
            )
            assert skill.description == "Previously known good metadata"
            assert "OLD-KNOWN-GOOD-BODY" in skill.path.read_text()
            from custom_components.extended_openai_conversation_responses.functions.file import (
                ReadFileFunction,
            )
            from homeassistant.helpers.template import Template

            body = await ReadFileFunction().execute(
                hass,
                {
                    "path": Template(
                        "{{extended_openai.skill_dir(name)}}/{{file}}", hass
                    )
                },
                {"name": "owned-skill", "file": "SKILL.md"},
                None,
                [],
            )
            assert "OLD-KNOWN-GOOD-BODY" in body["content"]
            assert not (manager.staging_dir / "owned-candidate").exists()
            (config / "verify.json").write_text(
                json.dumps(
                    {
                        "known_good_body_loaded": "OLD-KNOWN-GOOD-BODY"
                        in body["content"],
                        "catalogue_description": skill.description,
                        "recovered_kind": kind,
                    }
                )
            )
            assert not list(manager.staging_dir.glob("*.backup-*"))
            assert not list(manager.staging_dir.glob("*.remove-*"))
            assert not list(manager.staging_dir.glob("transaction-*.json"))
            staged = manager.staging_dir / "after-recovery"
            _write_skill(staged, "Healthy replacement")
            (staged / "SKILL.md").write_text(
                "---\ndescription: Healthy replacement\n---\nHEALTHY-NEW-BODY\n"
            )
            await manager.async_publish_staged_skill("owned-skill", staged)
            assert (
                "HEALTHY-NEW-BODY" in manager.get_skill("owned-skill").path.read_text()
            )
            assert await manager.async_remove_skill("owned-skill")
            assert manager.get_skill("owned-skill") is None and not target.exists()
    await hass.async_stop()


def _run(config, phase, kind):
    result = run_python_child(
        __file__, cwd=config, extra_env=_env(config, phase, kind), timeout=90
    )
    assert result.returncode == 0, (
        f"{phase}/{kind} failed:\n{result.stdout}\n{result.stderr}"
    )


def _kill(config, kind):
    with (config / "crash-output.log").open("w") as output:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=config,
            env=child_process_env(
                __file__, _env(config, "crash", kind), working_directory=config
            ),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 90
            while not (config / "paused").exists():
                assert process.poll() is None, (config / "crash-output.log").read_text()
                assert time.monotonic() < deadline, "native process cut never reached"
                time.sleep(
                    0.05
                )  # poll a durable boundary, never infer it from elapsed time
            cut = json.loads((config / "cut.json").read_text())
            if kind == "transfer":
                assert all(
                    Path(item["path"]).stat().st_size == item["size"] > 0
                    for item in cut["files"]
                )
            else:
                assert Path(cut["backup"]).is_dir()
            process.kill()
            assert process.wait(timeout=10) != 0
            return cut
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            (config / "paused").unlink(missing_ok=True)


@pytest.mark.parametrize("kind", ["transfer", "publish", "remove"])
@pytest.mark.usefixtures("socket_enabled")
def test_booted_filesystem_recovers_after_hard_kill(
    tmp_path, kind, stress_trace, monkeypatch
):
    # Parent-only fixtures import the repository integration. Keep them out of
    # the child before it selects and verifies the staged component below.
    from tests_stress.conftest import record

    config = tmp_path / "ha-config"
    destination = config / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(
        Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN, destination
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (config / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Filesystem recovery\nhttp:\n  server_host: 127.0.0.1\n  server_port: {port}\n"
    )
    (config / "preserved-temp").mkdir()
    (config / "preserved-temp" / "unrelated-sentinel").write_text("KEEP")
    if kind != "transfer":
        _run(config, "seed", kind)
    cycles = 3 if kind == "transfer" else 1
    active = None
    if kind == "transfer":
        import fcntl

        active_directory = (
            config / ".storage" / f"{DOMAIN}.transfer-staging" / f"owner-{'b' * 32}"
        )
        active_directory.mkdir(parents=True)
        active = (active_directory / "owner.lock").open("wb")
        fcntl.flock(active.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = active_directory / "extended-openai-backup-active.zip"
        payload.write_bytes(b"OTHER-LIVE-OWNER")
        monkeypatch.setenv(_ACTIVE_PAYLOAD, str(payload))
    try:
        for cycle in range(cycles):
            cut = _kill(config, kind)
            record(
                stress_trace, "filesystem_process_cut", kind=kind, cycle=cycle, cut=cut
            )
            _run(config, "verify", kind)
            if active is not None:
                assert payload.read_bytes() == b"OTHER-LIVE-OWNER"
            record(
                stress_trace,
                "filesystem_process_recovered",
                kind=kind,
                cycle=cycle,
                result=json.loads((config / "verify.json").read_text()),
            )
    finally:
        if active is not None:
            active.close()
    record(
        stress_trace,
        "summary",
        filesystem_recovery_kills=cycles,
        filesystem_recovery_cases=1,
        layer="process-boundary",
    )


if __name__ == "__main__" and os.environ.get(_PHASE):
    directory = Path(os.environ[_CONFIG]).resolve()
    sys.path.insert(0, str(directory))
    _assert_source_component(directory)
    asyncio.run(_child(directory, os.environ[_PHASE], os.environ[_KIND]))
