"""Resolve one immutable Home Assistant dev source for a validation cohort."""

from __future__ import annotations

import argparse
import re
import subprocess


def resolve_ha_dev_sha(requested: str = "") -> str:
    """Honor an explicit pin without consulting the moving branch."""
    if requested:
        sha = requested
    else:
        result = subprocess.run(
            [
                "git",
                "ls-remote",
                "https://github.com/home-assistant/core.git",
                "refs/heads/dev",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fields = result.stdout.split()
        if len(fields) != 2 or fields[1] != "refs/heads/dev":
            raise ValueError(
                "Home Assistant dev resolution did not return one branch SHA"
            )
        sha = fields[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(
            "Home Assistant dev SHA must be a full lowercase 40-character commit"
        )
    return sha


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", default="")
    args = parser.parse_args()
    print(resolve_ha_dev_sha(args.sha))
