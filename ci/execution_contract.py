"""Reviewed nightly case contract; missing evidence always fails closed."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests_stress/nightly_execution_contract.json"


def python_selections(workflow=None):
    text = (workflow or ROOT / ".github/workflows/enhanced-stress.yml").read_text(
        encoding="utf-8"
    )
    selections = {
        campaign: paths.split()
        for campaign, paths in re.findall(r"([a-z-]+)\) TEST_PATHS=\(([^)]+)\)", text)
    }
    jobs = {
        "lifecycle-matrix": "ha-lifecycle-matrix",
        "browser": "browser",
        "browser-firefox": "browser-engines",
        "browser-webkit": "browser-engines",
    }
    for campaign, job in jobs.items():
        section = text.split(f"\n  {job}:\n", 1)[1]
        section = re.split(r"\n  [a-z-]+:\n", section, maxsplit=1)[0]
        selections[campaign] = sorted(
            set(
                re.findall(
                    r"tests(?:_stress|_real_ha)?/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+)?",
                    section,
                )
            )
        )
    # Each engine job collects its own handoff parameter. The independently
    # reviewed catalog still requires both parameters across the two jobs.
    handoff = "tests_real_ha/test_browser_cross_engine_handoff_acceptance.py"
    for engine in ("firefox", "webkit"):
        campaign = f"browser-{engine}"
        selections[campaign] = sorted(
            [selector for selector in selections[campaign] if selector != handoff]
            + [
                f"{handoff}::test_cross_browser_state_handoff_survives_true_ha_restart[{engine}]"
            ]
        )
    lifecycle_runner = (ROOT / "ci/run_ha_lifecycle_contract.sh").read_text(
        encoding="utf-8"
    )
    selections["lifecycle-matrix"] = sorted(
        set(
            re.findall(
                r"tests(?:_stress|_real_ha)?/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+)?",
                lifecycle_runner,
            )
        )
    )
    return selections


def selected(node, selector):
    return (
        node == selector
        or node.startswith(selector + "::")
        or node.startswith(selector + "[")
        or node.startswith(selector.rstrip("/") + "/")
    )


def expected_cases(contract, campaign):
    # The reviewed selector catalog is the independent obligation boundary.
    # Never infer it from the live workflow: narrowing a workflow selector must
    # leave the already-reviewed required cases intact.
    selectors = contract.get("selections", {}).get(campaign, [])
    python = {
        node for node in contract["pytest"] if any(selected(node, p) for p in selectors)
    }
    browser = set(contract.get("browser", {}).get(campaign, []))
    return python | browser


def check_execution(item, contract=None):
    contract = contract or json.loads(CONTRACT.read_text(encoding="utf-8"))
    campaign = item["campaign"]
    expected = expected_cases(contract, campaign)
    errors = []
    if not expected:
        errors.append(f"{campaign}: no reviewed mandatory cases")
    by_node = defaultdict(list)
    for case in item.get("execution_cases", []):
        by_node[case["nodeid"]].append(case)
    allowances = contract.get("allowances", {})
    for node in sorted(expected):
        cases = by_node.get(node, [])
        allowance = allowances.get(node)
        if allowance and (
            not allowance.get("reason")
            or not allowance.get("review")
            or date.fromisoformat(allowance["expires"]) < date.today()
        ):
            errors.append(f"{node}: invalid/expired reviewed allowance")
            continue
        if not cases:
            errors.append(f"{node}: mandatory case absent")
            continue
        passed = any(
            case.get("collected")
            and case.get("executed")
            and case.get("outcome") == "passed"
            for case in cases
        )
        allowed = set(allowance.get("outcomes", [])) if allowance else set()
        if not passed and not (
            allowance
            and not allowance.get("requires_pass", True)
            and allowed
            and all(case.get("outcome") in allowed for case in cases)
        ):
            errors.append(
                f"{node}: mandatory case did not pass ({[case.get('outcome') for case in cases]})"
            )
        for case in cases:
            outcome = case.get("outcome")
            if outcome != "passed" and outcome not in allowed:
                errors.append(f"{node}: unexpected {outcome}")
    # Newly selected cases must be reviewed too. Reused fixture-only skip copies
    # are explicitly declared; absence of their required genuine pass still fails.
    for node in sorted(by_node.keys() - expected):
        reason = contract.get("fixture_only", {}).get(node)
        if not reason or any(
            case.get("outcome") != "skipped" for case in by_node[node]
        ):
            errors.append(f"{node}: collected case is not reviewed for {campaign}")
    for metric, minimum in contract.get("minimums", {}).get(campaign, {}).items():
        value = item.get("measured_totals", {}).get(metric, 0)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < minimum
        ):
            errors.append(f"{campaign}: {metric}={value}, required minimum {minimum}")
    return errors
