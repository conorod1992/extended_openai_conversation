"""Reuse the reviewed execution contract for standalone HA compatibility lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from candidate_evidence import check_candidate
from enhanced_evidence import checkout_sha
from execution_contract import CONTRACT, check_execution, expected_cases


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
