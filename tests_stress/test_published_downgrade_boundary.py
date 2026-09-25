"""A published older payload must refuse current-only backup state safely."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

_CHILD = "EOAI_DOWNGRADE_CHILD"
_COMPONENT = "DOWNGRADE_COMPONENT_DIR"
_DOCUMENT = "EOAI_DOWNGRADE_DOCUMENT"


def _older_release_child() -> None:
    # A fresh process imports only the staged tagged integration, never the
    # repository checkout's already-loaded module objects.
    root = Path(os.environ[_COMPONENT]).resolve().parents[1]
    sys.path.insert(0, str(root))
    from custom_components.extended_openai_conversation_responses import backup

    document = json.loads(Path(os.environ[_DOCUMENT]).read_text(encoding="utf-8"))
    try:
        backup.inspect_backup(document, document["agent"]["source_subentry_id"])
    except backup.BackupError as err:
        assert "unknown" in str(err).lower() or "unsupported" in str(err).lower(), err
    else:
        raise AssertionError("Older release silently accepted a later schema field")


if __name__ == "__main__" and os.environ.get(_CHILD):
    _older_release_child()


async def test_current_export_is_rejected_by_published_older_release(
    hass,
    tmp_path,
    stress_trace,
) -> None:
    import subprocess

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.extended_openai_conversation_responses import backup
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_SKIP_AUTHENTICATION,
        CONFIG_ENTRY_VERSION,
        DOMAIN,
    )
    from homeassistant.const import CONF_API_KEY
    from tests_real_ha.process_harness import child_process_env
    from tests_stress.conftest import record

    component = os.environ.get(_COMPONENT)
    assert component, "Nightly persistence campaign must stage a published payload"
    assert (Path(component) / "manifest.json").is_file()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Downgrade boundary",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {"future_agent_extension": {"min_reader": "develop"}},
                "subentry_type": "conversation",
                "title": "Downgrade agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    document = tmp_path / "current-backup.json"
    document.write_text(json.dumps(snapshot), encoding="utf-8")
    before = document.read_bytes()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env=child_process_env(
            __file__, {_CHILD: "1", _COMPONENT: component, _DOCUMENT: str(document)}
        ),
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f"older payload failed unsafely:\n{result.stdout}\n{result.stderr}"
    )
    assert document.read_bytes() == before
    assert subentry.data["future_agent_extension"] == {"min_reader": "develop"}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert next(iter(entry.subentries.values())).data["future_agent_extension"] == {
        "min_reader": "develop"
    }
    record(
        stress_trace,
        "published_downgrade_rejected",
        older_version=json.loads((Path(component) / "manifest.json").read_text())[
            "version"
        ],
        current_backup_version=backup.BACKUP_VERSION,
    )
