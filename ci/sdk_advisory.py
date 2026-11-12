"""Identify an advisory SDK range excluded by Home Assistant's exact pin."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys

from packaging.requirements import Requirement


def unavailable_reason(constraints: str, requested: str) -> str | None:
    """Skip only a proven incompatible OpenAI pin, leaving pip other failures."""
    candidate = Requirement(requested)
    if candidate.name != "openai":
        raise ValueError("The SDK advisory must request openai")
    for line in constraints.splitlines():
        line = line.partition("#")[0].strip()
        if not re.match(r"(?i)^openai(?:\s|[<>=!~;@\[]|$)", line):
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        for pin in requirement.specifier:
            if (
                pin.operator == "=="
                and "*" not in pin.version
                and not candidate.specifier.contains(pin.version, prereleases=True)
            ):
                return (
                    f"Home Assistant pins openai=={pin.version}; "
                    f"the advisory request {requested} cannot run in this environment."
                )
    return None


def main(requested: str) -> None:
    import homeassistant

    constraints = Path(homeassistant.__file__).parent / "package_constraints.txt"
    reason = unavailable_reason(constraints.read_text(encoding="utf-8"), requested)
    if reason is None:
        return
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"unavailable_reason={reason}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(
            f"## SDK advisory not run\n\n{reason}\n\nNo wire-test pass is claimed.\n"
        )
    print(reason)


if __name__ == "__main__":
    main(sys.argv[1])
