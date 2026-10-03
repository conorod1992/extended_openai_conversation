"""Overnight semantic scan using the existing genuine HA shell fixture."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ci.enhanced_evidence import (
    checkout_sha,
    environment_fingerprint,
    environment_identity,
)
from ci.frontend_latency.review import POLICY, check_accessibility
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    real_ha_shell as real_ha_shell,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EOAI_ACCESSIBILITY") != "1",
    reason="overnight semantic sweep only",
)


async def test_genuine_shell_accessibility_semantics(real_ha_shell):
    root = Path(__file__).resolve().parents[2]
    output = Path(
        os.environ.get(
            "EOAI_ACCESSIBILITY_OUTPUT", "latency-results/accessibility.json"
        )
    )
    await _run_playwright(
        repo_root=root,
        spec="ci/frontend_latency/accessibility.spec.mjs",
        config="ci/frontend_latency/playwright.config.mjs",
        env={
            **real_ha_shell["env"],
            "EOAI_ACCESSIBILITY_OUTPUT": str(output.resolve()),
        },
        failure_label="Genuine HA semantic accessibility sweep failed",
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    environment = environment_identity()
    environment["browser"] = result["browser_environment"]
    result.update(
        eoai_sha=checkout_sha(),
        environment=environment,
        environment_sha256=environment_fingerprint(environment),
    )
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    errors = check_accessibility(
        result, checkout_sha(), json.loads(POLICY.read_text(encoding="utf-8"))
    )
    assert not errors, "Genuine HA accessibility evidence rejected:\n" + "\n".join(
        errors
    )
