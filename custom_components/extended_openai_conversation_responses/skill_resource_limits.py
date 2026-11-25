"""Resource ceilings for local Skill discovery."""

from __future__ import annotations

from pathlib import Path
from stat import S_ISREG

from homeassistant.exceptions import HomeAssistantError

from .const import FILE_READ_SIZE_LIMIT

# These ceilings are intentionally generous so ordinary Skills are unaffected while
# malformed or unexpectedly large trees cannot consume unbounded memory/disk/CPU.
MAX_SKILL_DISCOVERY_ENTRIES = 4096
MAX_DISCOVERED_SKILLS = 512
# The canonical load_skill preset uses read_file. Do not advertise instructions
# that discovery can read but that the model's bounded file reader cannot load.
MAX_SKILL_METADATA_BYTES = FILE_READ_SIZE_LIMIT


def read_bounded_skill_text(path: Path) -> str:
    """Read one local SKILL.md without permitting an unbounded allocation."""
    try:
        if not S_ISREG(path.stat().st_mode):
            raise HomeAssistantError(f"Skill metadata `{path}` is not a regular file")
        with path.open("rb") as handle:
            content = handle.read(MAX_SKILL_METADATA_BYTES + 1)
    except OSError as err:
        raise HomeAssistantError(
            f"Unable to read Skill metadata `{path}`: {err}"
        ) from err

    if len(content) > MAX_SKILL_METADATA_BYTES:
        raise HomeAssistantError(
            f"Skill metadata `{path}` exceeds the {MAX_SKILL_METADATA_BYTES}-byte limit"
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as err:
        raise HomeAssistantError(f"Skill metadata `{path}` is not valid UTF-8") from err
