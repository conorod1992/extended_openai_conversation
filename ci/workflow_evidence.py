"""Save a compact exact-checkout envelope for the existing release matrices."""

from __future__ import annotations

import os
from pathlib import Path
import sys

try:
    from .enhanced_evidence import envelope, write_json
except ImportError:
    from enhanced_evidence import envelope, write_json


def main(kind: str, destination: Path) -> None:
    if kind not in {"upgrade", "sdk"}:
        raise SystemExit("Evidence kind must be upgrade or sdk")
    status = os.environ["ENHANCED_JOB_STATUS"]
    reason = os.environ.get("SDK_UNAVAILABLE_REASON")
    details = {}
    if (
        kind == "sdk"
        and os.environ["EVIDENCE_LANE"] == "latest-3x-early-warning"
        and reason
    ):
        # A constrained-out advisory is not a passing SDK wire run. Preserve
        # failures from setup or later steps rather than reclassifying them.
        if status == "success":
            status = "skipped"
        details["skip_reason"] = reason
    write_json(
        destination,
        envelope(
            campaign=f"release-{kind}",
            workflow_kind=kind,
            lane=os.environ["EVIDENCE_LANE"],
            status=status,
            upgrade_source_sha=os.environ.get("UPGRADE_SOURCE_SHA"),
            **details,
        ),
    )


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
