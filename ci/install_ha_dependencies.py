#!/usr/bin/env python3
"""Install Python requirements for HA integrations referenced by EOAI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import homeassistant


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the EOAI manifest.json to inspect.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    components = Path(next(iter(homeassistant.__path__))) / "components"
    requirements = set(manifest.get("requirements", []))
    pending = [
        *manifest.get("dependencies", []),
        *manifest.get("after_dependencies", []),
    ]
    visited: set[str] = set()

    while pending:
        domain = pending.pop()
        if domain in visited:
            continue
        visited.add(domain)

        manifest_path = components / domain / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(
                f"Home Assistant dependency manifest not found: {domain}"
            )

        dependency_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        requirements.update(dependency_manifest.get("requirements", []))
        pending.extend(dependency_manifest.get("dependencies", []))
        pending.extend(dependency_manifest.get("after_dependencies", []))

    print("Resolved HA dependency integrations:", ", ".join(sorted(visited)))
    if not requirements:
        return

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", *sorted(requirements)]
    )


if __name__ == "__main__":
    main()
