"""Separate collection and reproducible diagnostics for enhanced campaigns."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_real_ha.conftest import real_ha_prerequisites  # noqa: F401,E402


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--stress-seed", default=os.environ.get("STRESS_SEED"))
    parser.addoption(
        "--stress-intensity",
        choices=("normal", "heavy"),
        default=os.environ.get("STRESS_INTENSITY", "normal"),
    )


@pytest.fixture(scope="session")
def stress_seed(pytestconfig: pytest.Config) -> int:
    raw = pytestconfig.getoption("--stress-seed")
    seed = int(raw) if raw else secrets.randbits(32)
    print(
        f"\nENHANCED STRESS SEED={seed} INTENSITY={pytestconfig.getoption('--stress-intensity')}",
        flush=True,
    )
    return seed


@pytest.fixture(scope="session")
def stress_scale(pytestconfig: pytest.Config) -> int:
    return 4 if pytestconfig.getoption("--stress-intensity") == "heavy" else 1


@pytest.fixture
def stress_trace(request: pytest.FixtureRequest, stress_seed: int) -> list[dict]:
    trace: list[dict] = []
    yield trace
    report_dir = Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
    report_dir.mkdir(parents=True, exist_ok=True)
    name = (
        request.node.nodeid.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("[", "_")
        .replace("]", "_")
    )
    (report_dir / f"{name}.json").write_text(
        json.dumps(
            {"seed": stress_seed, "test": request.node.nodeid, "operations": trace},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(
        f"STRESS TRACE seed={stress_seed} test={request.node.nodeid} operations={len(trace)}",
        flush=True,
    )


def record(trace: list[dict], operation: str, **details: object) -> None:
    trace.append({"number": len(trace) + 1, "operation": operation, **details})
