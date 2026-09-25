"""Nightly clean installed-payload install/remove/reinstall acceptance."""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock, patch

from tests_real_ha.test_packaged_process_restart import (
    DOMAIN,
    _assert_packaged_module,
    _exercise_conversation,
)
from tests_stress.conftest import record

_PHASE = "EOAI_NIGHTLY_INSTALLED_CYCLE"


async def _installed_cycle(config_dir: Path, cycle: int) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    await hass.async_start()
    try:
        _assert_packaged_module(config_dir)
        assert not hass.config_entries.async_entries(DOMAIN)
        config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        const = importlib.import_module(f"custom_components.{DOMAIN}.const")
        with patch.object(
            config_flow, "get_authenticated_client", AsyncMock(return_value=object())
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            assert result["type"] is FlowResultType.FORM
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_NAME: f"Installed cycle {cycle}",
                    CONF_API_KEY: "sk-nightly-installed-cycle",
                    const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                    const.CONF_SKIP_AUTHENTICATION: True,
                    const.CONF_API_PROVIDER: "openai",
                },
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        subentries = [
            subentry
            for subentry in entry.subentries.values()
            if subentry.subentry_type == "conversation"
        ]
        assert len(subentries) == 1
        subentry = subentries[0]
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data=dict(subentry.data),
            title=f"Durable installed cycle {cycle}",
        )
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert next(iter(entry.subentries.values())).title == f"Durable installed cycle {cycle}"
        await _exercise_conversation(hass, entry.entry_id, f"Installed cycle {cycle} works.")

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert not hass.config_entries.async_entries(DOMAIN)
    finally:
        await hass.async_stop()


def test_clean_installed_payload_repeated_removal_and_reinstall(
    tmp_path: Path, stress_trace: list[dict]
) -> None:
    """Each cycle uses a new HA process and only the delivered component tree."""
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Installed Lifecycle\nrecorder:\n",
        encoding="utf-8",
    )

    for cycle in range(3):
        assert not destination.exists()
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        assert (destination / "manifest.json").is_file()
        env = os.environ.copy()
        env[_PHASE] = str(cycle)
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=config_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        record(
            stress_trace,
            "installed_cycle",
            cycle=cycle,
            returncode=result.returncode,
            stdout=result.stdout[-4000:],
            stderr=result.stderr[-4000:],
        )
        assert result.returncode == 0, (
            f"installed lifecycle cycle {cycle} failed\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
        shutil.rmtree(destination)


if __name__ == "__main__" and _PHASE in os.environ:
    asyncio.run(_installed_cycle(Path.cwd(), int(os.environ[_PHASE])))
