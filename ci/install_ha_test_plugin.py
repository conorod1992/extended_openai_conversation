#!/usr/bin/env python3
"""Install exact published fixtures, or regenerate the pinned release-day bridge."""

from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen
import venv
import zipfile

try:
    from .resolve_ha_test_plugin import (
        CORE_SHA,
        GENERATED_HA_VERSION,
        GENERATED_PLUGIN_VERSION,
        GENERATOR_SHA,
    )
except ImportError:
    from resolve_ha_test_plugin import (
        CORE_SHA,
        GENERATED_HA_VERSION,
        GENERATED_PLUGIN_VERSION,
        GENERATOR_SHA,
    )


def extract_source(repository: str, sha: str, destination: Path) -> None:
    """Download immutable source and reject unsafe archive members."""
    with urlopen(
        f"https://codeload.github.com/{repository}/tar.gz/{sha}", timeout=120
    ) as response:
        archive = destination.parent / f"{sha}.tar.gz"
        archive.write_bytes(response.read())
    with tarfile.open(archive) as source:
        source.extractall(destination.parent, filter="data")
    (destination.parent / f"{repository.rsplit('/', 1)[-1]}-{sha}").rename(destination)


def verify_wheel(wheel: Path) -> None:
    """Fail closed if regenerated metadata or actual fixture version drift."""
    with zipfile.ZipFile(wheel) as package:
        metadata_path = next(
            name for name in package.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(package.read(metadata_path).decode())
        if metadata["Version"] != GENERATED_PLUGIN_VERSION:
            raise RuntimeError(
                "Generated fixture package version differs from its identity"
            )
        if f"homeassistant=={GENERATED_HA_VERSION}" not in metadata.get_all(
            "Requires-Dist", []
        ):
            raise RuntimeError(
                "Generated fixture package does not pin exact Home Assistant"
            )
        const = package.read("pytest_homeassistant_custom_component/const.py").decode()
        constants = {
            node.target.id: ast.literal_eval(node.value)
            for node in ast.parse(const).body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in {"MAJOR_VERSION", "MINOR_VERSION", "PATCH_VERSION"}
        }
        fixture_version = ".".join(
            str(constants.get(name, ""))
            for name in ("MAJOR_VERSION", "MINOR_VERSION", "PATCH_VERSION")
        )
        if fixture_version != GENERATED_HA_VERSION:
            raise RuntimeError(
                "Generated fixtures do not come from the expected Core version"
            )
        provenance = json.loads(
            package.read(
                "pytest_homeassistant_custom_component/eoai_source_identity.json"
            )
        )
        if provenance != {
            "generator_sha": GENERATOR_SHA,
            "core_sha": CORE_SHA,
            "homeassistant": GENERATED_HA_VERSION,
        }:
            raise RuntimeError("Generated fixture source provenance differs")


def build_generated_plugin(destination: Path) -> Path:
    """Run the unmodified upstream generator on exact released Core sources."""
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eoai-ha-fixtures-") as temporary:
        root = Path(temporary)
        source = root / "generator"
        extract_source(
            "MatthewFlamm/pytest-homeassistant-custom-component", GENERATOR_SHA, source
        )
        extract_source("home-assistant/core", CORE_SHA, source / "tmp_dir")
        environment = root / "build-env"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "click==8.1.3",
                "GitPython==3.1.14",
                "setuptools",
                "wheel",
            ],
            check=True,
        )
        # Upstream selects the newest tag by cloning Core. Supply the immutable
        # release archive instead; all fixture transformations remain upstream.
        launcher = source / "generate_exact.py"
        launcher.write_text(
            "import runpy, sys\n"
            "sys.path[:0] = ['generate_phacc', 'src']\n"
            "import ha\n"
            f"ha.prepare_homeassistant = lambda: {GENERATED_HA_VERSION!r}\n"
            "sys.argv = ['generate_phacc/generate_phacc.py', '--regen']\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n",
            encoding="utf-8",
        )
        subprocess.run([str(python), str(launcher)], cwd=source, check=True)
        (source / "version").write_text(GENERATED_PLUGIN_VERSION, encoding="utf-8")
        provenance = {
            "generator_sha": GENERATOR_SHA,
            "core_sha": CORE_SHA,
            "homeassistant": GENERATED_HA_VERSION,
        }
        (
            source
            / "src/pytest_homeassistant_custom_component/eoai_source_identity.json"
        ).write_text(json.dumps(provenance), encoding="utf-8")
        # Include source identity in the wheel, alongside upstream licenses.
        with (source / "setup.cfg").open("a", encoding="utf-8") as config:
            config.write(
                "\n[options.package_data]\npytest_homeassistant_custom_component = eoai_source_identity.json\n"
            )
        subprocess.run(
            [
                str(python),
                "setup.py",
                "bdist_wheel",
                "--dist-dir",
                str(destination.resolve()),
            ],
            cwd=source,
            check=True,
        )
    wheel = next(
        destination.glob(
            f"pytest_homeassistant_custom_component-{GENERATED_PLUGIN_VERSION}-*.whl"
        )
    )
    verify_wheel(wheel)
    print("Generated exact released fixtures: " + json.dumps(provenance), flush=True)
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ha_version")
    parser.add_argument("plugin_version")
    parser.add_argument("--build-only", type=Path)
    args = parser.parse_args()
    if args.plugin_version != GENERATED_PLUGIN_VERSION:
        if args.build_only:
            parser.error("--build-only is only valid for the pinned generated bridge")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                f"pytest-homeassistant-custom-component=={args.plugin_version}",
                f"homeassistant=={args.ha_version}",
            ],
            check=True,
        )
        return
    if args.ha_version != GENERATED_HA_VERSION:
        parser.error(
            "Generated fixtures require their exact released Home Assistant version"
        )
    if args.build_only:
        build_generated_plugin(args.build_only)
        return
    with tempfile.TemporaryDirectory(prefix="eoai-ha-wheel-") as temporary:
        wheel = build_generated_plugin(Path(temporary))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                str(wheel),
                f"homeassistant=={args.ha_version}",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
