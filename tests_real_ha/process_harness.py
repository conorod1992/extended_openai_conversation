"""Shared subprocess helpers for Real Home Assistant acceptance tests."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import sys


def child_process_env(
    test_file: str | Path,
    extra_env: Mapping[str, str],
    *,
    working_directory: str | Path,
) -> dict[str, str]:
    """Prefer the staged HA config while preserving repository test imports."""
    env = os.environ.copy()
    env.update(extra_env)

    repo_root = Path(test_file).resolve().parents[1]
    ensure_staged_custom_components_package(working_directory)
    existing_pythonpath = env.get("PYTHONPATH")
    import_roots = [str(Path(working_directory).resolve()), str(repo_root)]
    if existing_pythonpath:
        import_roots.extend(existing_pythonpath.split(os.pathsep))
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(import_roots))
    return env


def ensure_staged_custom_components_package(config_dir: str | Path) -> Path:
    """Make staged components win over the repository's namespace package."""
    package_root = Path(config_dir).resolve() / "custom_components"
    package_root.mkdir(parents=True, exist_ok=True)
    initializer = package_root / "__init__.py"
    initializer.touch(exist_ok=True)
    return initializer


def run_python_child(
    test_file: str | Path,
    *,
    cwd: str | Path,
    extra_env: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run one independent Python child process for a Real HA acceptance phase."""
    path = Path(test_file).resolve()
    return subprocess.run(
        [sys.executable, str(path)],
        cwd=Path(cwd),
        env=child_process_env(path, extra_env, working_directory=cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
