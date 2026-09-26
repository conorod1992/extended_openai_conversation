"""Separate collection and reproducible diagnostics for enhanced campaigns."""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
import secrets
import sys
from time import monotonic

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ci.enhanced_evidence import envelope, safe, write_json  # noqa: E402
from tests_real_ha.conftest import real_ha_prerequisites  # noqa: F401,E402


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item._enhanced_call_report = report


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
    started = monotonic()
    yield trace
    report_dir = Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
    report_dir.mkdir(parents=True, exist_ok=True)
    readable = (
        request.node.nodeid.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("[", "_")
        .replace("]", "_")
    )
    # Keep the basename below common 255-byte filesystem limits while retaining
    # the full node ID in the JSON report for diagnostics.
    digest = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
    name = f"{readable[:160]}-{digest}"
    report = getattr(request.node, "_enhanced_call_report", None)
    hass = request.node.funcargs.get("hass")
    health = None
    if hass is not None:
        health = {
            "ha_state_count": len(hass.states.async_all()),
            "eoai_manager_counts": {
                str(key): len(value)
                for key, value in hass.data.items()
                if isinstance(key, str)
                and key.startswith("extended_openai_conversation_responses.")
                and isinstance(value, dict)
            },
        }
    write_json(
        report_dir / f"{name}.json",
        {
            **envelope(seed=stress_seed),
            "test": request.node.nodeid,
            "outcome": "failed" if report and report.failed else "passed",
            "duration_seconds": round(monotonic() - started, 3),
            "failure": str(report.longrepr).splitlines()[-1]
            if report and report.failed
            else None,
            "health": health,
            "operations": trace,
        },
    )
    print(
        f"STRESS TRACE seed={stress_seed} test={request.node.nodeid} operations={len(trace)}",
        flush=True,
    )


def record(trace: list[dict], operation: str, **details: object) -> None:
    trace.append(safe({"number": len(trace) + 1, "operation": operation, **details}))
