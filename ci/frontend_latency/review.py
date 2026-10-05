"""Fail closed on missing overnight measurements; report performance variance."""

from __future__ import annotations

import ast
from collections import Counter
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import sys

from ci.enhanced_evidence import environment_fingerprint

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "overnight_policy.json"


def routes():
    return re.findall(
        r'\{name: "([^"]+)", path:', (ROOT / "routes.mjs").read_text(encoding="utf-8")
    )


def backend_operations():
    tree = ast.parse((ROOT / "test_latency_diagnostics.py").read_text(encoding="utf-8"))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "collect"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def check_identity(item, sha):
    errors = []
    if not re.fullmatch(r"[0-9a-f]{40}", sha or "") or item.get("eoai_sha") != sha:
        errors.append(f"Intended SHA {sha}, measured {item.get('eoai_sha')}")
    environment = item.get("environment", {})
    if not environment or item.get("environment_sha256") != environment_fingerprint(
        environment
    ):
        errors.append("Missing/invalid environment fingerprint")
    return errors


def check_latency(item, sha, harness_sha, baseline=False):
    errors = check_identity(item, sha)
    if item.get("harness_sha") != harness_sha:
        errors.append("Latency harness does not match intended candidate")
    runs = item.get("runs")
    if runs not in (3, 5):
        return [*errors, "Expected three or five cold samples"]
    if set(item.get("backend", {})) != backend_operations():
        errors.append("Backend operation set is incomplete or unreviewed")
    for name, entry in item.get("backend", {}).items():
        if baseline and entry.get("supported") is False:
            continue
        samples = entry.get("samples_ms", [])
        if (
            len(samples) != runs
            or not all(finite(value) for value in samples)
            or not finite(entry.get("median_ms"))
        ):
            errors.append(f"Incomplete backend samples: {name}")
    samples = item.get("browser", {}).get("samples", [])
    if set(sample.get("route") for sample in samples) != set(routes()):
        errors.append("Cold route set is incomplete or unreviewed")
    for route in routes():
        selected = [sample for sample in samples if sample.get("route") == route]
        if baseline and len(selected) == 1 and selected[0].get("supported") is False:
            continue
        if len(selected) != runs or {
            sample.get("iteration") for sample in selected
        } != set(range(1, runs + 1)):
            errors.append(f"Missing/duplicate cold samples: {route}")
        for sample in selected:
            if (
                sample.get("supported") is not True
                or not finite(sample.get("wall_ready_ms"))
                or sample.get("failures")
            ):
                errors.append(f"Failed route measurement: {route}")
    return errors


def scan_key(scan):
    return (scan["route"], scan["state"], scan["theme"], scan["width"])


def expected_scans(*, diagnostic_picker=False):
    source = (ROOT / "accessibility.spec.mjs").read_text(encoding="utf-8")
    narrow = json.loads(re.search(r"NARROW_ROUTES = (\[.*?\]);", source).group(1))
    interactive = json.loads(
        re.search(r"INTERACTIVE_STATES = (\{.*?\});", source).group(1)
    )
    if diagnostic_picker:
        interactive["assistant-voice"] = ["picker-populated"]
    assert set(narrow) <= set(routes())
    keys = set()
    for theme in ("light", "dark"):
        for width, names in ((1280, routes()), (390, narrow)):
            for route in names:
                keys.add((route, "route", theme, width))
                for state in interactive.get(route, []):
                    keys.add((route, state, theme, width))
                if route in {"capabilities-functions", "capabilities-request-rules"}:
                    keys.add(
                        (
                            route,
                            "tool-editor"
                            if route.endswith("functions")
                            else "rule-editor",
                            theme,
                            width,
                        )
                    )
    return keys


def reviewed_state_key(key):
    """Reuse only unchanged exact findings from the corresponding reviewed state."""
    route, state, theme, width = key
    original = {
        ("capabilities-functions", "tool-invalid"): "tool-editor",
        ("capabilities-functions", "tool-save-error"): "tool-editor",
        ("capabilities-functions", "destructive-confirmation"): "route",
        ("capabilities-functions", "destructive-error"): "route",
        ("assistant-voice", "picker-populated"): "route",
        ("usage-maintenance-backup-restore", "restore-confirmation"): "route",
    }.get((route, state), state)
    return route, original, theme, width


def check_accessibility(item, sha, policy):
    errors = check_identity(item, sha)
    scans = item.get("scans", [])
    keys = [scan_key(scan) for scan in scans]
    if not isinstance(item.get("diagnostic_picker", False), bool):
        errors.append("Invalid accessibility diagnostic profile")
    if len(keys) != len(set(keys)) or set(keys) != expected_scans(
        diagnostic_picker=item.get("diagnostic_picker", False)
    ):
        errors.append(
            "Mandatory accessibility route/theme/viewport/editor scan missing or duplicated"
        )
    allowances = list(policy.get("accessibility_allowances", []))
    if policy.get("accessibility_baseline"):
        baseline = json.loads(
            (ROOT / policy["accessibility_baseline"]).read_text(encoding="utf-8")
        )
        assert re.fullmatch(r"[0-9a-f]{40}", baseline["source_sha"])
        assert baseline["axe_version"] == "4.11.0"
        for finding in baseline["findings"]:
            for case, count in finding["cases"].items():
                route, state, theme, width = case.split("|")
                allowances.append(
                    {
                        "case": [route, state, theme, int(width)],
                        "rule": finding["rule"],
                        "target": finding["target"],
                        "impact": finding["impact"],
                        "max_nodes": count,
                        "reason": finding.get("reason", baseline["reason"]),
                        "reviewed_by": baseline["reviewed_by"],
                        "review_until": baseline["review_until"],
                    }
                )
    for allowance in allowances:
        try:
            valid = (
                allowance["reason"].strip()
                and allowance["reviewed_by"].strip()
                and date.fromisoformat(allowance["review_until"]) >= date.today()
            )
        except KeyError, ValueError:
            valid = False
        if not valid:
            errors.append("Invalid/expired accessibility allowance")
    observed = Counter()
    for scan in scans:
        colour = scan.get("theme_colour", {})
        brightness = colour.get("brightness")
        if (
            not colour.get("background")
            or not finite(brightness)
            or (brightness < 128) != (scan["theme"] == "dark")
        ):
            errors.append(f"Rendered HA theme was not verified: {scan_key(scan)}")
        if (
            scan.get("axe_version") != "4.11.0"
            or not isinstance(scan.get("passes"), int)
            or scan["passes"] <= 0
        ):
            errors.append(
                f"Accessibility scanner did not evaluate rules: {scan_key(scan)}"
            )
        if not isinstance(scan.get("violations"), list) or not isinstance(
            scan.get("incomplete", []), list
        ):
            errors.append(
                f"Accessibility scan has no machine-readable findings: {scan_key(scan)}"
            )
        for violation in scan.get("violations", []):
            if violation.get("impact") not in {"serious", "critical"}:
                continue
            for node in violation["nodes"]:
                signature = (
                    scan_key(scan),
                    violation["id"],
                    json.dumps(node["target"], sort_keys=True),
                )
                observed[signature] += 1
                if not any(
                    tuple(allowance["case"]) == reviewed_state_key(signature[0])
                    and allowance["rule"] == signature[1]
                    and json.dumps(allowance["target"], sort_keys=True) == signature[2]
                    and allowance["impact"] == violation["impact"]
                    for allowance in allowances
                ):
                    errors.append(
                        f"New {violation['impact']} accessibility violation: {signature}"
                    )
    declared = {
        (tuple(a["case"]), a["rule"], json.dumps(a["target"], sort_keys=True))
        for a in allowances
    }
    for signature, count in observed.items():
        matching = [
            a
            for a in allowances
            if (tuple(a["case"]), a["rule"], json.dumps(a["target"], sort_keys=True))
            == (reviewed_state_key(signature[0]), signature[1], signature[2])
        ]
        if matching and count > max(a.get("max_nodes", 1) for a in matching):
            errors.append(f"Accessibility violation count grew: {signature}: {count}")
    for obsolete in declared - observed.keys():
        errors.append(
            f"Accessibility allowance became obsolete; review/remove: {obsolete}"
        )
    return errors


def main():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    directory = Path(sys.argv[1])
    required = [directory / f"{label}.json" for label in ("baseline", "current")]
    required.append(directory / "accessibility.json")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(
            "Overnight frontend evidence is incomplete; missing required file(s):\n"
            + "\n".join(missing)
        )
    errors = []
    for label, sha in (
        ("baseline", os.environ["BASELINE_SHA"]),
        ("current", os.environ["CURRENT_SHA"]),
    ):
        item = json.loads((directory / f"{label}.json").read_text(encoding="utf-8"))
        errors += check_latency(
            item, sha, os.environ["CURRENT_SHA"], baseline=label == "baseline"
        )
        if label == "baseline":
            baseline_environment = item.get("environment_sha256")
        elif baseline_environment != item.get("environment_sha256"):
            errors.append("Baseline/candidate environments differ")
    item = json.loads((directory / "accessibility.json").read_text(encoding="utf-8"))
    errors += check_accessibility(item, os.environ["CURRENT_SHA"], policy)
    if item.get("environment_sha256") != baseline_environment:
        errors.append("Accessibility/latency environments differ")
    if errors:
        raise SystemExit("Overnight frontend evidence rejected:\n" + "\n".join(errors))
    print(
        f"Validated exact-source latency samples and {len(item['scans'])} semantic scans. Performance policy: reporting-only."
    )


if __name__ == "__main__":
    main()
