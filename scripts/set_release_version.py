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
FRONTEND_VERSION_FILE = (
    ROOT
    / "custom_components/extended_openai_conversation_responses/frontend_version.py"
)
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_PATTERN = re.compile(r'(^  "version": ")[^"]+("$)', re.MULTILINE)
EXTRACT_PATTERN = re.compile(r'^  "version": "([^"]+)"$', re.MULTILINE)
FRONTEND_VERSION_PATTERN = re.compile(
    r'^(FRONTEND_VERSION = ")[^"]+("$)', re.MULTILINE
)
FRONTEND_EXTRACT_PATTERN = re.compile(
    r'^FRONTEND_VERSION = "([^"]+)"$', re.MULTILINE
)


def _manifest_version(root: Path) -> str:
    path = root / MANIFEST.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    matches = EXTRACT_PATTERN.findall(text)
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one integration version in "
            f"{path.relative_to(root)}; found {len(matches)}"
        )
    return matches[0]


def _frontend_version(root: Path) -> str:
    path = root / FRONTEND_VERSION_FILE.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    matches = FRONTEND_EXTRACT_PATTERN.findall(text)
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one frontend asset version in "
            f"{path.relative_to(root)}; found {len(matches)}"
        )
    return matches[0]


def current_version(root: Path = ROOT) -> str:
    """Return the tracked integration version and verify asset metadata matches."""
    version = _manifest_version(root)
    frontend_version = _frontend_version(root)
    for label, value in (
        ("Tracked release version", version),
        ("Frontend asset version", frontend_version),
    ):
        if not SEMVER.fullmatch(value):
            raise RuntimeError(f"{label} is not X.Y.Z: {value!r}")
    if frontend_version != version:
        raise RuntimeError(
            "Frontend asset version does not match manifest version: "
            f"{frontend_version!r} != {version!r}"
        )
    return version


def _replace_one(
    path: Path, pattern: re.Pattern[str], version: str, label: str
) -> None:
    text = path.read_text(encoding="utf-8")

    def replacement(match: re.Match[str]) -> str:
        return f"{match.group(1)}{version}{match.group(2)}"

    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one writable {label} in {path.relative_to(ROOT)}; "
            f"found {count}"
        )
    path.write_text(updated, encoding="utf-8")


def set_release_version(version: str, root: Path = ROOT) -> None:
    """Set every tracked release-version field to ``version``."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Release version must use X.Y.Z format, got {version!r}")

    current_version(root)
    manifest = root / MANIFEST.relative_to(ROOT)
    frontend_version_file = root / FRONTEND_VERSION_FILE.relative_to(ROOT)
    _replace_one(manifest, VERSION_PATTERN, version, "integration version")
    _replace_one(
        frontend_version_file,
        FRONTEND_VERSION_PATTERN,
        version,
        "frontend asset version",
    )

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
