"""Reuse the reviewed execution contract for standalone HA compatibility lanes."""

from __future__ import annotations

from importlib.metadata import distribution
import json
import os
from pathlib import Path
import re
import sys

try:
    from .candidate_evidence import check_candidate
    from .enhanced_evidence import checkout_sha
    from .execution_contract import CONTRACT, check_execution, expected_cases
except ImportError:
    from candidate_evidence import check_candidate
    from enhanced_evidence import checkout_sha
    from execution_contract import CONTRACT, check_execution, expected_cases


def check_stable_point(item, point, intended_version):
    if point != "stable":
        return []
    tested = item.get("environment", {}).get("packages", {}).get("homeassistant")
    if not re.fullmatch(r"[0-9]{4}\.[0-9]+\.[0-9]+", intended_version or ""):
        return ["Stable lane has no resolved final-release HA version"]
    if tested != intended_version:
        return [f"Stable HA intended {intended_version}, tested {tested}"]
    return []


def check_frontend_point(item, intended_version):
    tested = (
        item.get("environment", {}).get("packages", {}).get("home-assistant-frontend")
    )
    if not intended_version or tested != intended_version:
        return [f"HA frontend intended {intended_version}, tested {tested}"]
    return []


def main():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    campaign = os.environ["STRESS_CAMPAIGN"]
    if sys.argv[1] == "select":
        print(" ".join(contract["selections"][campaign]))
        return 0
    item = json.loads(
        (Path(sys.argv[2]) / "certification.json").read_text(encoding="utf-8")
    )
    errors = check_candidate(item, checkout_sha()) + check_execution(item, contract)
    errors += check_stable_point(
        item, os.environ.get("HA_POINT"), os.environ.get("HA_STABLE_VERSION")
    )
    if os.environ.get("HA_POINT") not in {"oldest", "stable", "dev"}:
        errors.append("Compatibility lane has no reviewed HA point")
    if campaign.startswith("native-browser-"):
        frontend_manifest = Path(
            distribution("homeassistant").locate_file(
                "homeassistant/components/frontend/manifest.json"
            )
        )
        requirement = next(
            value
            for value in json.loads(frontend_manifest.read_text(encoding="utf-8"))[
                "requirements"
            ]
            if value.startswith("home-assistant-frontend==")
        )
        errors += check_frontend_point(item, requirement.split("==", 1)[1])
    if item.get("campaign") != campaign:
        errors.append(f"Expected campaign {campaign}, received {item.get('campaign')}")
    if item["status"] != "success":
        errors.append("Compatibility job did not succeed")
    print(
        json.dumps(
            {
                "campaign": campaign,
                "expected_cases": sorted(expected_cases(contract, campaign)),
                "errors": errors,
            },
            indent=2,
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
