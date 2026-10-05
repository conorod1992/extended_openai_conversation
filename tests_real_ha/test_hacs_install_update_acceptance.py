"""Genuine HACS install/update acceptance for a populated EOAI installation."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from tests_real_ha import test_release_upgrade_acceptance as upgrade_helpers

DOMAIN = "extended_openai_conversation_responses"
HACS_DOMAIN = "hacs"
REPOSITORY = "conorod1992/extended_openai_conversation"

_CHILD_PHASE_ENV = "HACS_ACCEPTANCE_CHILD_PHASE"
_CONFIG_DIR_ENV = "HACS_ACCEPTANCE_CONFIG_DIR"
_HACS_COMPONENT_ENV = "HACS_ACCEPTANCE_COMPONENT_DIR"
_TOKEN_ENV = "HACS_ACCEPTANCE_GITHUB_TOKEN"
_RELEASE_ENV = "HACS_ACCEPTANCE_RELEASE"
_CANDIDATE_REF_ENV = "HACS_ACCEPTANCE_CANDIDATE_REF"
_CANDIDATE_ROOT_ENV = "HACS_ACCEPTANCE_CANDIDATE_ROOT"
_EVIDENCE_ENV = "HACS_ACCEPTANCE_EVIDENCE_FILE"

_STATE_FILE = "upgrade-acceptance-state.json"
_HACS_STATE_FILE = "hacs-acceptance-state.json"
_OBSOLETE_RELEASE_FILES = (
    "guest_performance.py",
    "frontend/management-bootstrap.js",
)

if not os.environ.get(_CHILD_PHASE_ENV):
    import pytest

    pytestmark = pytest.mark.skipif(
        not all(
            os.environ.get(name)
            for name in (
                _HACS_COMPONENT_ENV,
                _TOKEN_ENV,
                _RELEASE_ENV,
                _CANDIDATE_REF_ENV,
                _CANDIDATE_ROOT_ENV,
            )
        ),
        reason="requires HACS component, GitHub token, release, and candidate ref",
    )


def _component_dir(config_dir: Path) -> Path:
    return config_dir / "custom_components" / DOMAIN


def _manifest(component: Path) -> dict[str, Any]:
    return json.loads((component / "manifest.json").read_text(encoding="utf-8"))


def _tree_digest(component: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(component.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(component)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _candidate_component() -> Path:
    return (
        Path(os.environ[_CANDIDATE_ROOT_ENV]).resolve()
        / "custom_components"
        / DOMAIN
    )


def _assert_candidate_tree(config_dir: Path) -> None:
    installed = _component_dir(config_dir)
    candidate = _candidate_component()
    assert _tree_digest(installed) == _tree_digest(candidate), (
        "HACS update did not leave the exact candidate component tree installed"
    )
    assert _manifest(installed) == _manifest(candidate)
    for relative in (
        "frontend/management-panel.js",
        "frontend/dist/manifest.json",
    ):
        assert (installed / relative).read_bytes() == (candidate / relative).read_bytes()


async def _ensure_hacs(hass: Any) -> Any:
    """Set up HACS through its real config flow, faking only human OAuth consent."""
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    entries = hass.config_entries.async_entries(HACS_DOMAIN)
    if entries:
        assert len(entries) == 1
        entry = entries[0]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        hacs = hass.data[HACS_DOMAIN]
        assert not hacs.system.disabled
        return hacs

    config_flow = importlib.import_module("custom_components.hacs.config_flow")
    token = os.environ[_TOKEN_ENV]

    class AutomatedDeviceAuthorization:
        async def register(self) -> Any:
            return SimpleNamespace(
                data=SimpleNamespace(
                    device_code="hacs-ci-device",
                    user_code="HACS-CI",
                )
            )

        async def activation(self, *, device_code: str) -> Any:
            assert device_code == "hacs-ci-device"
            return SimpleNamespace(
                data=SimpleNamespace(access_token=token)
            )

    with patch.object(
        config_flow,
        "GitHubDeviceAPI",
        return_value=AutomatedDeviceAuthorization(),
    ):
        result = await hass.config_entries.flow.async_init(
            HACS_DOMAIN,
            context={"source": SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "acc_logs": True,
                "acc_addons": True,
                "acc_untested": True,
                "acc_disable": True,
            },
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        flow_id = result["flow_id"]
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
        assert result["type"] is FlowResultType.CREATE_ENTRY

    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(HACS_DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["token"] == token

    hacs = hass.data[HACS_DOMAIN]
    assert not hacs.system.disabled
    assert hacs.configuration.token == token
    return hacs

async def _eoai_repository(hacs: Any) -> Any:
    """Return the real HACS repository object, registering it when necessary."""
    repository = hacs.repositories.get_by_full_name(REPOSITORY)
    if repository is None:
        await hacs.async_register_repository(
            repository_full_name=REPOSITORY,
            category="integration",
        )
        repository = hacs.repositories.get_by_full_name(REPOSITORY)
    assert repository is not None
    assert repository.data.full_name == REPOSITORY
    return repository


async def _install_release(hass: Any, config_dir: Path) -> None:
    hacs = await _ensure_hacs(hass)
    repository = await _eoai_repository(hacs)
    release = os.environ[_RELEASE_ENV]

    assert not _component_dir(config_dir).exists()
    await repository.async_download_repository(ref=release)
    await hacs.data.async_write(force=True)

    installed = _component_dir(config_dir)
    assert installed.is_dir()
    assert _manifest(installed)["version"] == release
    assert repository.data.installed
    assert repository.data.installed_version == release

    for relative in _OBSOLETE_RELEASE_FILES:
        assert (installed / relative).is_file(), (
            f"published release fixture no longer contains expected obsolete file: {relative}"
        )

    state = {
        "release": release,
        "candidate_ref": os.environ[_CANDIDATE_REF_ENV],
        "hacs_repository_id": str(repository.data.id),
        "release_digest": _tree_digest(installed),
        "obsolete_files": list(_OBSOLETE_RELEASE_FILES),
    }
    (config_dir / _HACS_STATE_FILE).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _populate_release(hass: Any, config_dir: Path) -> None:
    """Use the released EOAI runtime to create representative durable state."""
    await _ensure_hacs(hass)
    installed = _component_dir(config_dir)
    assert _manifest(installed)["version"] == os.environ[_RELEASE_ENV]
    await upgrade_helpers._released_phase(hass, config_dir)

    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    assert state["entry_id"]
    assert state["subentry_id"]
    assert state["memory_marker"]
    assert state["knowledge_marker"]
    assert state["request_rule_marker"]
    assert "upgrade_marker" in state["function_tool_names"]


async def _update_to_candidate(hass: Any, config_dir: Path) -> None:
    """Ask HACS itself to replace the released tree with the candidate ref."""
    await hass.async_block_till_done()
    assert (config_dir / _STATE_FILE).is_file(), "release state was not populated"

    installed = _component_dir(config_dir)
    for relative in _OBSOLETE_RELEASE_FILES:
        assert (installed / relative).is_file()

    hacs = await _ensure_hacs(hass)
    repository = await _eoai_repository(hacs)
    previous_id = str(repository.data.id)
    assert repository.data.installed

    candidate_ref = os.environ[_CANDIDATE_REF_ENV]
    await repository.async_download_repository(ref=candidate_ref)
    await hacs.data.async_write(force=True)

    # This is the critical mixed-tree boundary. HACS must remove files that were
    # present in the old release but are no longer part of the candidate.
    for relative in _OBSOLETE_RELEASE_FILES:
        assert not (installed / relative).exists(), (
            f"HACS left obsolete release file active after update: {relative}"
        )

    _assert_candidate_tree(config_dir)
    assert str(repository.data.id) == previous_id

    hacs_state = json.loads(
        (config_dir / _HACS_STATE_FILE).read_text(encoding="utf-8")
    )
    hacs_state.update(
        {
            "candidate_digest": _tree_digest(installed),
            "repository_id_after_update": str(repository.data.id),
            "installed_commit_after_update": repository.data.installed_commit,
            "installed_version_after_update": repository.data.installed_version,
        }
    )
    (config_dir / _HACS_STATE_FILE).write_text(
        json.dumps(hacs_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _candidate_migrate(hass: Any, config_dir: Path) -> None:
    """Restart onto the HACS-installed candidate and prove populated state."""
    await _ensure_hacs(hass)
    _assert_candidate_tree(config_dir)

    # Removed Python modules must not remain importable from the HACS-installed tree.
    for module_name in (
        f"custom_components.{DOMAIN}.guest_performance",
        f"custom_components.{DOMAIN}.lifecycle_optimizations",
    ):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError(f"obsolete module remained importable: {module_name}")

    await upgrade_helpers._candidate_migration_phase(hass, config_dir)


async def _candidate_restart(hass: Any, config_dir: Path) -> None:
    """One more cold boot proves the HACS update is durably healthy."""
    await _ensure_hacs(hass)
    _assert_candidate_tree(config_dir)
    await upgrade_helpers._candidate_restart_phase(hass, config_dir)


async def _child_main() -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.helpers import recorder as recorder_helper

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    sys.path.insert(0, str(config_dir))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)
    await hass.async_start()

    try:
        phase = os.environ[_CHILD_PHASE_ENV]
        if phase == "install-release":
            await _install_release(hass, config_dir)
        elif phase == "populate-release":
            await _populate_release(hass, config_dir)
        elif phase == "hacs-update":
            await _update_to_candidate(hass, config_dir)
        elif phase == "candidate-migrate":
            await _candidate_migrate(hass, config_dir)
        elif phase == "candidate-restart":
            await _candidate_restart(hass, config_dir)
        else:
            raise AssertionError(f"unknown HACS acceptance phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def _run_child(config_dir: Path, phase: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env[_CHILD_PHASE_ENV] = phase
    env[_CONFIG_DIR_ENV] = str(config_dir)
    repo_root = Path(__file__).resolve().parents[1]
    existing = env.get("PYTHONPATH")
    roots = [str(config_dir), str(repo_root)]
    if existing:
        roots.extend(existing.split(os.pathsep))
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(roots))

    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=360,
        check=False,
    )


def _assert_child_ok(result: subprocess.CompletedProcess[str], phase: str) -> None:
    assert result.returncode == 0, (
        f"HACS acceptance phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


def _stage_hacs(config_dir: Path) -> None:
    source = Path(os.environ[_HACS_COMPONENT_ENV]).resolve()
    destination = config_dir / "custom_components" / HACS_DOMAIN
    destination.parent.mkdir(parents=True, exist_ok=True)
    (destination.parent / "__init__.py").touch()
    shutil.copytree(source, destination)
    assert (destination / "manifest.json").is_file()


def _write_evidence(config_dir: Path) -> None:
    target = os.environ.get(_EVIDENCE_ENV)
    if not target:
        return
    hacs_state = json.loads(
        (config_dir / _HACS_STATE_FILE).read_text(encoding="utf-8")
    )
    eoai_state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    payload = {
        "candidate_sha": os.environ.get("GITHUB_SHA"),
        "candidate_ref": os.environ[_CANDIDATE_REF_ENV],
        "published_release": os.environ[_RELEASE_ENV],
        "hacs_repository_id": hacs_state["hacs_repository_id"],
        "repository_id_after_update": hacs_state["repository_id_after_update"],
        "release_digest": hacs_state["release_digest"],
        "candidate_digest": hacs_state["candidate_digest"],
        "obsolete_files": hacs_state["obsolete_files"],
        "entry_id": eoai_state["entry_id"],
        "subentry_id": eoai_state["subentry_id"],
        "memory_marker": eoai_state["memory_marker"],
        "knowledge_source_id": eoai_state["knowledge_source_id"],
    }
    Path(target).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_hacs_installs_release_updates_candidate_and_preserves_state(
    socket_enabled: Any,
    tmp_path: Path,
) -> None:
    """Run a genuine HACS release install and candidate update across HA restarts."""
    del socket_enabled
    config_dir = tmp_path / "ha-config"
    config_dir.mkdir()
    _stage_hacs(config_dir)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: Genuine HACS Acceptance\n"
        "recorder:\n",
        encoding="utf-8",
    )

    for phase in (
        "install-release",
        "populate-release",
        "hacs-update",
        "candidate-migrate",
        "candidate-restart",
    ):
        result = _run_child(config_dir, phase)
        _assert_child_ok(result, phase)

    _assert_candidate_tree(config_dir)
    _write_evidence(config_dir)


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE_ENV):
    asyncio.run(_child_main())
