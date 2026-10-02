#!/usr/bin/env python3
"""Create an auditable identity for a prebuilt Python test environment."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import subprocess
import sys


def _requirements(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        values.append(stripped.split(" #", 1)[0].strip())
    return sorted(set(values))


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


def _installed_python_packages() -> list[str]:
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


def _matches(built: dict[str, object], current: dict[str, object]) -> bool:
    """Require exact dependency, recipe, base, and installed-package identity."""
    return built == current


def identity(
    requirements: Path,
    manifest: Path,
    recipes: list[Path],
    python_version: str,
    base_image: str,
    extra: list[str],
) -> dict[str, object]:
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    inputs: dict[str, object] = {
        "schema": "eoai-python-environment/v1",
        "python_version": python_version,
        "base_image": base_image,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "os_release": _os_release(),
        },
        "system_packages": _system_packages(),
        "installed_python_packages": _installed_python_packages(),
        "requirements": _requirements(requirements),
        "integration_dependencies": sorted(set(manifest_data.get("dependencies", []))),
        "integration_after_dependencies": sorted(
            set(manifest_data.get("after_dependencies", []))
        ),
        "integration_requirements": sorted(set(manifest_data.get("requirements", []))),
        "recipes": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(recipes, key=lambda item: item.name)
        },
        "extra": sorted(extra),
    }
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return {**inputs, "sha256": hashlib.sha256(canonical).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, action="append", default=[])
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--extra", action="append", default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", type=Path)
    mode.add_argument("--check", type=Path)
    args = parser.parse_args()

    result = identity(
        args.requirements,
        args.manifest,
        args.recipe,
        args.python_version,
        args.base_image,
        args.extra,
    )
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(result["sha256"])
        return 0

    built = json.loads(args.check.read_text(encoding="utf-8"))
    if not _matches(built, result):
        print(
            "Prebuilt Python environment identity does not match current inputs.",
            file=sys.stderr,
        )
        print(f"built={built.get('sha256', '<missing>')}", file=sys.stderr)
        print(f"current={result['sha256']}", file=sys.stderr)
        return 1
    print(f"Prebuilt Python environment matches {result['sha256']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
