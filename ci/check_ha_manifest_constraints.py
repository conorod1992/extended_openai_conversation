"""Check every integration manifest requirement against one HA constraints file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("constraints", type=Path)
    args = parser.parse_args()
    manifests = sorted(Path("custom_components").glob("*/manifest.json"))
    if not manifests:
        parser.error("no integration manifests found")

    failures = []
    for manifest in manifests:
        requirements = json.loads(manifest.read_text(encoding="utf-8")).get(
            "requirements", []
        )
        for requirement in requirements:
            print(f"{manifest.parent.name}: checking {requirement}", flush=True)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--dry-run",
                    "--ignore-installed",
                    "--no-deps",
                    "--disable-pip-version-check",
                    "--constraint",
                    str(args.constraints),
                    requirement,
                ],
                check=False,
            )
            if result.returncode:
                failures.append(f"{manifest.parent.name}: {requirement}")

    if failures:
        print(
            "Could not resolve these requirements under Home Assistant constraints:\n"
            + "\n".join(f"  {failure}" for failure in failures),
            file=sys.stderr,
        )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
