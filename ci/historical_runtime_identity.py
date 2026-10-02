#!/usr/bin/env python3
"""Fingerprint the isolated runtime used to exercise a published EOAI release."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distributions, version
import json
from pathlib import Path
import platform
import subprocess
import sys


def _system_packages() -> list[str]:
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${binary:Package}=${Version}\\n"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError, subprocess.CalledProcessError:
        return []
    return sorted(line for line in result.stdout.splitlines() if line)


def _installed_packages() -> list[str]:
    return sorted(
        f"{dist.metadata['Name'].lower()}=={dist.version}"
        for dist in distributions()
        if dist.metadata.get("Name")
    )


def _os_release() -> dict[str, str]:
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def identity(
    manifest_path: Path,
    release_tag: str,
    release_sha: str,
    homeassistant_version: str,
    base_image: str,
    recipes: list[Path],
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs: dict[str, object] = {
        "schema": "eoai-historical-runtime/v1",
        "release_tag": release_tag,
        "release_sha": release_sha,
        "homeassistant_version": homeassistant_version,
        "python_version": platform.python_version(),
        "pip_version": version("pip"),
        "base_image": base_image,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "os_release": _os_release(),
        },
        "system_packages": _system_packages(),
        "manifest_requirements": sorted(set(manifest.get("requirements", []))),
        "manifest_dependencies": sorted(set(manifest.get("dependencies", []))),
        "manifest_after_dependencies": sorted(
            set(manifest.get("after_dependencies", []))
        ),
        "recipes": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(recipes, key=lambda item: item.name)
        },
    }
    input_payload = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return {
        **inputs,
        "installed_python_packages": _installed_packages(),
        "inputs_sha256": hashlib.sha256(input_payload).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--homeassistant-version", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--recipe", type=Path, action="append", default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print-key", action="store_true")
    mode.add_argument("--write", type=Path)
    mode.add_argument("--check", type=Path)
    args = parser.parse_args()
    current = identity(
        args.manifest,
        args.release_tag,
        args.release_sha,
        args.homeassistant_version,
        args.base_image,
        args.recipe,
    )
    if args.print_key:
        print(f"eoai-historical-runtime-{current['inputs_sha256']}")
        return 0
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    built = json.loads(args.check.read_text(encoding="utf-8"))
    if built != current:
        print(
            "Cached historical runtime identity does not match its contents.",
            file=sys.stderr,
        )
        return 1
    print(f"Historical runtime matches {current['inputs_sha256']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
