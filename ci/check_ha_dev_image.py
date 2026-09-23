#!/usr/bin/env python3
"""Accept a published HA dev image only when its Core snapshot is recent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_SNAPSHOT_AGE = timedelta(hours=24)


def main(image: str, current_sha: str) -> int:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
        check=True,
        capture_output=True,
        text=True,
    )
    labels = json.loads(result.stdout)
    snapshot_sha = labels.get("org.opencontainers.image.revision", "")
    created = labels.get("org.opencontainers.image.created", "")
    if not snapshot_sha or not created:
        print("HA dev image has no snapshot metadata.")
        return 1

    try:
        built_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        print(f"HA dev image has invalid creation time: {created}")
        return 1
    if built_at.tzinfo is None:
        print(f"HA dev image has no creation timezone: {created}")
        return 1

    age = datetime.now(UTC) - built_at
    print(f"Published HA dev snapshot: {snapshot_sha} (built {created})")
    print(f"Current HA Core dev commit: {current_sha}")
    if age < timedelta(minutes=-5):
        print("HA dev image creation time is in the future.")
        return 1
    if snapshot_sha != current_sha and age > MAX_SNAPSHOT_AGE:
        print("Published HA dev snapshot is older than 24 hours.")
        return 1

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(
                f"HA dev image: `{snapshot_sha}` (built {created}); "
                f"current Core dev: `{current_sha}`.\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
