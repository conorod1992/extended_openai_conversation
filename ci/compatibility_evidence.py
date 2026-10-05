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


def check_stable_point(
    item, point, intended_version, *, require_reported_version=False
):
    if point != "stable":
        return []
    tested = item.get("environment", {}).get("packages", {}).get("homeassistant")
    if not re.fullmatch(r"[0-9]{4}\.[0-9]+\.[0-9]+", intended_version or ""):
        return ["Stable lane has no resolved final-release HA version"]
    if tested != intended_version:
        return [f"Stable HA intended {intended_version}, tested {tested}"]
    if require_reported_version and item.get("ha_version") != intended_version:
        return [
            f"Stable HA intended {intended_version}, reported {item.get('ha_version')}"
        ]
    return []


def check_frontend_point(item, intended_version):
    tested = (
        item.get("environment", {}).get("packages", {}).get("home-assistant-frontend")
    )
    if not intended_version or tested != intended_version:
        return [f"HA frontend intended {intended_version}, tested {tested}"]
    return []


def check_ha_environment(item, *, version=None, source_sha=None, plugin_version=None):
    """Check intended HA identities in every Python execution, not just the summary."""
    errors = []
    envelopes = [item, *(run for run in item.get("execution_runs", []) if run.get("runner") == "pytest")]
    for envelope in envelopes:
        identity = envelope.get("environment", {})
        packages = identity.get("packages", {})
        if version is not None and packages.get("homeassistant") != version:
            errors.append(f"HA intended {version}, tested {packages.get('homeassistant')}")
        if source_sha is not None and identity.get("homeassistant_source_commit") != source_sha:
            errors.append(f"HA dev intended {source_sha}, tested {identity.get('homeassistant_source_commit')}")
        if plugin_version is not None and packages.get("pytest-homeassistant-custom-component") != plugin_version:
            errors.append(f"HA fixture intended {plugin_version}, tested {packages.get('pytest-homeassistant-custom-component')}")
    return errors


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
    point = os.environ.get("HA_POINT")
    if point == "oldest":
        minimum = json.loads((Path(__file__).resolve().parents[1] / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]
        errors += check_ha_environment(item, version=minimum)
    elif point == "stable":
        errors += check_ha_environment(item, version=os.environ.get("HA_STABLE_VERSION"))
    elif point == "dev":
        intended = os.environ.get("HA_DEV_SHA")
        if not intended:
            errors.append("Dev compatibility lane has no intended HA source SHA")
        else:
            errors += check_ha_environment(item, source_sha=intended)
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
