#!/usr/bin/env python3
"""Validate and stage an Extended OpenAI Conversation release version.

Normal development keeps committed version metadata at the most recent intended
release. The Release workflow takes the target version once, uses this helper to
stage every tracked release-version field, commits any required bump, and creates
the matching GitHub release.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components/extended_openai_conversation_responses/manifest.json"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_PATTERN = re.compile(r'(^  "version": ")[^"]+("$)', re.MULTILINE)
EXTRACT_PATTERN = re.compile(r'^  "version": "([^"]+)"$', re.MULTILINE)


def current_version(root: Path = ROOT) -> str:
    """Return the tracked integration version, failing on an unexpected manifest."""
    path = root / MANIFEST.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    matches = EXTRACT_PATTERN.findall(text)
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one integration version in "
            f"{path.relative_to(root)}; found {len(matches)}"
        )
    version = matches[0]
    if not SEMVER.fullmatch(version):
        raise RuntimeError(f"Tracked release version is not X.Y.Z: {version!r}")
    return version


def set_release_version(version: str, root: Path = ROOT) -> None:
    """Set every tracked release-version field to ``version``."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Release version must use X.Y.Z format, got {version!r}")

    current_version(root)
    path = root / MANIFEST.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")

    def replacement(match: re.Match[str]) -> str:
        return f"{match.group(1)}{version}{match.group(2)}"

    updated, count = VERSION_PATTERN.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(
            "Expected exactly one writable integration version in "
            f"{path.relative_to(root)}; found {count}"
        )
    path.write_text(updated, encoding="utf-8")

    staged = current_version(root)
    if staged != version:
        raise RuntimeError(f"Version staging produced {staged!r}, expected {version!r}")


def main() -> None:
    """Run the release-version command-line helper."""
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="target X.Y.Z release version")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify the tracked release version",
    )
    args = parser.parse_args()

    if args.check:
        if args.version is not None:
            parser.error("--check does not accept a target version")
        print(current_version())  # noqa: T201 - CLI output consumed by workflows
        return
    if args.version is None:
        parser.error("a target version is required unless --check is used")

    before = current_version()
    set_release_version(args.version)
    print(  # noqa: T201 - explicit CLI status output
        f"Staged release version {before} -> {args.version}"
    )


if __name__ == "__main__":
    main()
