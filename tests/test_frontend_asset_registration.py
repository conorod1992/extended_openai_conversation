"""The production frontend manifest owns shipped asset registration."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.extended_openai_conversation_responses import frontend_assets

COMPONENT = Path(frontend_assets.__file__).parent
FRONTEND = COMPONENT / "frontend"
DIST = FRONTEND / "dist"


def _manifest() -> dict[str, dict]:
    return json.loads((DIST / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_has_one_public_management_entry() -> None:
    entries = [entry for entry in _manifest().values() if entry.get("isEntry") is True]
    assert [entry.get("name") for entry in entries] == ["management"]
    assert entries[0]["file"].startswith("assets/management-")
    assert (DIST / entries[0]["file"]).is_file()


def test_manifest_dynamic_chunks_all_exist() -> None:
    manifest = _manifest()
    for source, entry in manifest.items():
        assert (DIST / entry["file"]).is_file(), source
        for dependency in (*entry.get("imports", []), *entry.get("dynamicImports", [])):
            assert dependency in manifest, f"{source} references missing {dependency}"
            assert (DIST / manifest[dependency]["file"]).is_file()
