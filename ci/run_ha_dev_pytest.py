#!/usr/bin/env python3
"""Run genuine HA-dev tests with the helpers shipped in that exact Core source."""

from __future__ import annotations

import argparse
import importlib
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    from .ha_dev_snapshot import resolve_ha_dev_sha
    from .install_ha_test_plugin import extract_source
except ImportError:
    from ha_dev_snapshot import resolve_ha_dev_sha
    from install_ha_test_plugin import extract_source


def run_pytest(core_root: Path, args: list[str]) -> int:
    """Use native fixtures; keep existing test imports referring to those helpers."""
    import pytest
    from pytest_homeassistant import _paths

    import homeassistant  # noqa: F401 — initialize Core's httpx alias first

    _paths.TESTS_DIR = core_root / "tests"
    # Core installs recorder/time/JSON patches before helper imports. Loading
    # recorder helpers first would violate the plugin's startup assertions.
    native_plugin = importlib.import_module("pytest_homeassistant.plugins")
    # Existing tests use PHACC import paths. Alias only the modules they import,
    # preserving identity with the modules used by the native fixture plugin.
    for suffix in ("", ".common", ".components.recorder.common"):
        sys.modules[f"pytest_homeassistant_custom_component{suffix}"] = (
            importlib.import_module(f"pytest_homeassistant{suffix}")
        )
    return pytest.main(["-p", "no:homeassistant", *args], plugins=[native_plugin])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--core-sha")
    source.add_argument("--core-root", type=Path)
    options, args = parser.parse_known_args()
    if options.core_root:
        return run_pytest(options.core_root, args)
    sha = resolve_ha_dev_sha(options.core_sha)
    with tempfile.TemporaryDirectory(prefix="eoai-dev-fixtures-") as temporary:
        core_root = Path(temporary) / "core"
        extract_source("home-assistant/core", sha, core_root)
        # Pin the already-installed Core version: helper installation must not
        # replace the immutable dev runtime with a published stable release.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                str(core_root / "packages/pytest-home-assistant"),
                f"homeassistant=={version('homeassistant')}",
            ],
            check=True,
        )
        # Start fresh after pip updates pytest and its dependencies. Keep Core's
        # test assets alive until the child finishes, including failure teardown.
        return subprocess.run(
            [sys.executable, __file__, "--core-root", str(core_root), *args],
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
