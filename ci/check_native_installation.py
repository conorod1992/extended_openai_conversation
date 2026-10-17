#!/usr/bin/env python3
"""Exercise HA's real custom-integration installer in a clean Core environment.

Run before installing EOAI/test requirements. Core's own dependencies are expected
already to be present; no custom constraint filtering or package replacement occurs.
"""

import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from packaging.requirements import Requirement

from homeassistant.core import HomeAssistant
from homeassistant.requirements import async_process_requirements


async def check(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        hass = HomeAssistant(directory)
        try:
            await async_process_requirements(
                hass, manifest["domain"], manifest["requirements"], is_built_in=False
            )
            for value in manifest["requirements"]:
                requirement = Requirement(value)
                installed = version(requirement.name)
                if installed not in requirement.specifier:
                    raise RuntimeError(f"Native installation did not satisfy {value}")
                print(f"Native installation: {requirement.name}=={installed}")
        finally:
            await hass.async_stop(force=True)


if __name__ == "__main__":
    asyncio.run(check(Path(sys.argv[1])))
