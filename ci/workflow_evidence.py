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
    write_json(
        destination,
        envelope(
            campaign=f"release-{kind}",
            workflow_kind=kind,
            lane=os.environ["EVIDENCE_LANE"],
            status=os.environ["ENHANCED_JOB_STATUS"],
            upgrade_source_sha=os.environ.get("UPGRADE_SOURCE_SHA"),
        ),
    )


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
