"""Contract checks for isolated Home Assistant subprocess imports."""

from __future__ import annotations

import os
from pathlib import Path

from tests_real_ha.process_harness import (
    child_process_env,
    ensure_staged_custom_components_package,
)


def test_child_imports_staged_config_before_repository_source(tmp_path: Path) -> None:
    env = child_process_env(__file__, {}, working_directory=tmp_path)
    roots = env["PYTHONPATH"].split(os.pathsep)
    assert roots[0] == str(tmp_path.resolve())
    assert roots[1] == str(Path(__file__).resolve().parents[1])


def test_staged_components_are_a_regular_package(tmp_path: Path) -> None:
    initializer = ensure_staged_custom_components_package(tmp_path)
    assert initializer == tmp_path / "custom_components" / "__init__.py"
    assert initializer.is_file()
