#!/usr/bin/env python3
"""Validate HA-dev dependencies while allowing the intentional pytest-HA pin mismatch."""

from __future__ import annotations

from importlib.metadata import requires, version
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PLUGIN = "pytest-homeassistant-custom-component"


def allowed_plugin_mismatch(line: str) -> bool:
    prefix = f"{PLUGIN} "
    marker = " has requirement "
    if not line.startswith(prefix) or marker not in line:
        return False

    requirement_text = line.split(marker, 1)[1].split(", but you have ", 1)[0]
    requirement = Requirement(requirement_text)
    name = canonicalize_name(requirement.name)
    if name == "homeassistant":
        return True

    installed = version(requirement.name)
    for spec in requires("homeassistant") or []:
        ha_requirement = Requirement(spec)
        if canonicalize_name(ha_requirement.name) != name:
            continue
        if ha_requirement.marker is not None and not ha_requirement.marker.evaluate():
            continue
        if installed in ha_requirement.specifier:
            return True
    return False


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("No dependency conflicts found.")
        return 0
    lines = [
        line.strip()
        for line in (result.stdout + "\n" + result.stderr).splitlines()
        if line.strip()
    ]

    unexpected = [line for line in lines if not allowed_plugin_mismatch(line)]
    if unexpected:
        print("\n".join(unexpected), file=sys.stderr)
        return result.returncode or 1

    for line in lines:
        print(f"Allowed HA-dev compatibility mismatch: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
