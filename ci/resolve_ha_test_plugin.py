#!/usr/bin/env python3
"""Resolve the newest HA test plugin release built for one exact HA version."""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.request import Request, urlopen


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"\d+(?:\.\d+)+", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "EOAI-CI-environment-resolver/1"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def resolve_ha_test_plugin(
    ha_version: str,
    project: dict[str, Any],
    get_release: Any = _get_json,
) -> str:
    """Find the highest non-yanked release whose metadata pins the requested HA."""
    releases = project.get("releases", {})
    candidates = []
    for version, files in releases.items():
        numeric = _numeric_version(version)
        if (
            numeric is None
            or not files
            or all(file.get("yanked", False) for file in files)
        ):
            continue
        candidates.append((numeric, version))

    for _, version in sorted(candidates, reverse=True):
        metadata = get_release(
            f"https://pypi.org/pypi/pytest-homeassistant-custom-component/{version}/json"
        )["info"]
        for requirement in metadata.get("requires_dist") or []:
            match = re.match(
                r"^\s*homeassistant\s*==\s*([^;\s]+)", requirement, re.IGNORECASE
            )
            if match and match.group(1) == ha_version:
                return version

    raise RuntimeError(
        f"No published pytest-homeassistant-custom-component release pins "
        f"Home Assistant {ha_version}."
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <homeassistant-version>", file=sys.stderr)
        return 2
    ha_version = sys.argv[1]
    project = _get_json(
        "https://pypi.org/pypi/pytest-homeassistant-custom-component/json"
    )
    print(resolve_ha_test_plugin(ha_version, project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
