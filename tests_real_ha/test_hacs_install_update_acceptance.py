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

# Imported helpers are also used by a standalone historical HA child.
if os.environ.get("HACS_ACCEPTANCE_CHILD_PHASE"):
    os.environ.setdefault("UPGRADE_ACCEPTANCE_CHILD_PHASE", "hacs-helper")

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


def _candidate_tree_status(config_dir: Path) -> dict[str, Any]:
    """Prove every candidate file is exact and report any HACS-retained orphans."""
    installed = _component_dir(config_dir)
    candidate = _candidate_component()
    candidate_files = {
        str(path.relative_to(candidate))
        for path in candidate.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    installed_files = {
        str(path.relative_to(installed))
        for path in installed.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    for relative in candidate_files:
        assert (installed / relative).read_bytes() == (candidate / relative).read_bytes(), (
            f"HACS-installed candidate file differs from checkout: {relative}"
        )

    assert _manifest(installed) == _manifest(candidate)
    for relative in (
        "frontend/management-panel.js",
        "frontend/dist/manifest.json",
    ):
        assert (installed / relative).read_bytes() == (candidate / relative).read_bytes()

    return {
        "candidate_digest": _tree_digest(candidate),
        "candidate_file_count": len(candidate_files),
        "retained_orphans": sorted(installed_files - candidate_files),
    }


def _assert_obsolete_files_inert(config_dir: Path) -> None:
    """Old files may remain physically after HACS update, but cannot stay active."""
    installed = _component_dir(config_dir)

    frontend_manifest = (
        installed / "frontend" / "dist" / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "management-bootstrap" not in frontend_manifest

    # Candidate code must not reference the retired modules/assets. This turns a
    # HACS-retained orphan into inert disk debris rather than executable candidate code.
    retired_needles = (
        "guest_performance",
        "management-bootstrap",
    )
    for path in installed.rglob("*"):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or str(path.relative_to(installed)) in _OBSOLETE_RELEASE_FILES
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in retired_needles:
            assert needle not in text, (
                f"candidate file still references retired HACS orphan {needle}: {path}"
            )


def _assert_obsolete_modules_not_loaded() -> None:
    """A cold boot onto the candidate must not load retired release modules."""
    assert f"custom_components.{DOMAIN}.guest_performance" not in sys.modules
    assert f"custom_components.{DOMAIN}.lifecycle_optimizations" not in sys.modules


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
    activation_release = asyncio.Event()

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
            # Keep the mocked OAuth task pending until the config flow has
            # returned SHOW_PROGRESS. An immediately completed task races with
            # Home Assistant's progress handling and can turn this first result
            # into SHOW_PROGRESS_DONE before the test receives it.
            await activation_release.wait()
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
        assert result["type"] is FlowResultType.SHOW_PROGRESS, result
        flow_id = result["flow_id"]
        activation_release.set()
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
        assert result["type"] is FlowResultType.CREATE_ENTRY, result

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

    # HACS currently overlays repository archives and can retain orphaned files.
    # Record that physical state, but require the installed candidate files to be
    # byte-for-byte exact and the retired files to have no active references.
    tree_status = _candidate_tree_status(config_dir)
    retained_selected = [
        relative
        for relative in _OBSOLETE_RELEASE_FILES
        if (installed / relative).exists()
    ]
    _assert_obsolete_files_inert(config_dir)
    assert str(repository.data.id) == previous_id

    hacs_state = json.loads(
        (config_dir / _HACS_STATE_FILE).read_text(encoding="utf-8")
    )
    hacs_state.update(
        {
            "candidate_digest": tree_status["candidate_digest"],
            "candidate_file_count": tree_status["candidate_file_count"],
            "retained_orphans": tree_status["retained_orphans"],
            "retained_selected_obsolete_files": retained_selected,
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
    _assert_obsolete_modules_not_loaded()
    _candidate_tree_status(config_dir)
    _assert_obsolete_files_inert(config_dir)
    await upgrade_helpers._candidate_migration_phase(hass, config_dir)


async def _candidate_restart(hass: Any, config_dir: Path) -> None:
    """One more cold boot proves the HACS update is durably healthy."""
    await _ensure_hacs(hass)
    _assert_obsolete_modules_not_loaded()
    _candidate_tree_status(config_dir)
    _assert_obsolete_files_inert(config_dir)
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

    interpreter = (
        os.environ.get("HACS_ACCEPTANCE_RELEASED_PYTHON", sys.executable)
        if phase in {"install-release", "populate-release", "hacs-update"}
        else sys.executable
    )
    return subprocess.run(
        [interpreter, str(Path(__file__).resolve())],
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
        "retained_orphans": hacs_state["retained_orphans"],
        "retained_selected_obsolete_files": hacs_state[
            "retained_selected_obsolete_files"
        ],
        "entry_id": eoai_state["entry_id"],
        "subentry_id": eoai_state["subentry_id"],
        "memory_marker": eoai_state["memory_marker"],
        "knowledge_source_id": eoai_state["knowledge_source_id"],
    }
    Path(target).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )



def _simulate_interrupted_candidate_overlay(config_dir: Path) -> dict[str, Any]:
    """Create a deterministic mixed release/candidate tree without touching HACS metadata."""
    installed = _component_dir(config_dir)
    candidate = _candidate_component()
    differing = []
    for candidate_path in sorted(candidate.rglob("*")):
        if not candidate_path.is_file() or "__pycache__" in candidate_path.parts:
            continue
        relative = candidate_path.relative_to(candidate)
        installed_path = installed / relative
        if (
            not installed_path.is_file()
            or installed_path.read_bytes() != candidate_path.read_bytes()
        ):
            differing.append(relative)
    assert len(differing) >= 4, "candidate must differ materially from published release"

    # Approximate a replacement interrupted after candidate files have begun
    # overwriting the installed release but before HACS commits repository state.
    chosen = differing[: max(2, len(differing) // 3)]
    for relative in chosen:
        source = candidate / relative
        target = installed / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    untouched = [relative for relative in differing if relative not in chosen]
    assert untouched
    assert any(
        (installed / relative).is_file()
        and (installed / relative).read_bytes() != (candidate / relative).read_bytes()
        for relative in untouched
    ), "interrupted fixture must retain at least one release-generation file"
    assert all(
        (installed / relative).read_bytes() == (candidate / relative).read_bytes()
        for relative in chosen
    )
    return {
        "overwritten_candidate_files": [str(item) for item in chosen],
        "remaining_release_files": [str(item) for item in untouched],
    }


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

    _candidate_tree_status(config_dir)
    _assert_obsolete_files_inert(config_dir)
    _write_evidence(config_dir)




def test_hacs_recovers_from_interrupted_candidate_replacement(
    socket_enabled: Any,
    tmp_path: Path,
) -> None:
    """A partially overlaid EOAI tree is repaired by the next genuine HACS update."""
    del socket_enabled
    config_dir = tmp_path / "ha-config-interrupted"
    config_dir.mkdir()
    _stage_hacs(config_dir)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: Interrupted HACS Replacement Acceptance\n"
        "recorder:\n",
        encoding="utf-8",
    )

    for phase in ("install-release", "populate-release"):
        result = _run_child(config_dir, phase)
        _assert_child_ok(result, phase)

    hacs_state_before = json.loads(
        (config_dir / _HACS_STATE_FILE).read_text(encoding="utf-8")
    )
    mixed = _simulate_interrupted_candidate_overlay(config_dir)
    assert mixed["overwritten_candidate_files"]
    assert mixed["remaining_release_files"]

    # HACS still believes the published release owns this repository generation.
    hacs_state_after_mix = json.loads(
        (config_dir / _HACS_STATE_FILE).read_text(encoding="utf-8")
    )
    assert hacs_state_after_mix == hacs_state_before

    # A normal subsequent HACS update must converge the mixed filesystem onto the
    # exact candidate generation, preserve EOAI's populated state, and remain
    # healthy across another cold start.
    for phase in ("hacs-update", "candidate-migrate", "candidate-restart"):
        result = _run_child(config_dir, phase)
        _assert_child_ok(result, phase)

    status = _candidate_tree_status(config_dir)
    _assert_obsolete_files_inert(config_dir)
    assert status["candidate_file_count"] > 0


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE_ENV):
    asyncio.run(_child_main())
