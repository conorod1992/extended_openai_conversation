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
) -> dict[str, str]:
    """Return a child environment with the repository import path preserved."""
    env = os.environ.copy()
    env.update(extra_env)

    repo_root = Path(test_file).resolve().parents[1]
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not existing_pythonpath
        else os.pathsep.join((str(repo_root), existing_pythonpath))
    )
    return env


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
        env=child_process_env(path, extra_env),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
