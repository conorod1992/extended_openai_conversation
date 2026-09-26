"""Controlled failures run only by the manual/nightly diagnostics campaign."""

import pytest

from ci.enhanced_evidence import CANARIES
from tests_stress.conftest import record


@pytest.mark.parametrize("boundary", ["python", "provider", "lifecycle", "resource"])
def test_failure_artifact_survives_partial_trace(stress_trace, boundary):
    record(stress_trace, "begin", boundary=boundary, api_key=CANARIES[0])
    if boundary == "provider":
        record(stress_trace, "fault", phase="after_tool_effect", kind="disconnect")
    if boundary in {"lifecycle", "resource"}:
        record(
            stress_trace,
            "resource_snapshot",
            baseline_services=12,
            peak_services=13,
            after_services=13,
        )
    record(
        stress_trace,
        "before_assertion",
        private_content=CANARIES[1],
        knowledge_content=CANARIES[2],
        prompt=CANARIES[3],
    )
    pytest.fail(f"controlled {boundary} failure {CANARIES[0]}")
