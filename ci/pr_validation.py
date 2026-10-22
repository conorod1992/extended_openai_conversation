"""Select reusable PR lanes and reject failed, missing or incorrectly skipped work."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath

POLICY = Path(__file__).with_name("pr_workflows.json")
DEPENDENCY_GROUPS = Path(__file__).with_name("pr_dependency_groups.json")
CONTROL_PATHS = {
    "ci/pr_validation.py",
    "ci/pr_workflows.json",
    "ci/pr_dependency_groups.json",
    ".github/workflows/pr-validation.yml",
}


def matches(path: str, patterns: list[str]) -> bool:
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def select(
    paths: list[str],
    base: str,
    policy: dict | None = None,
    dependency_groups: dict | None = None,
) -> dict[str, bool]:
    policy = (
        policy if policy is not None else json.loads(POLICY.read_text(encoding="utf-8"))
    )
    groups = (
        dependency_groups
        if dependency_groups is not None
        else json.loads(DEPENDENCY_GROUPS.read_text(encoding="utf-8"))
    )
    if not isinstance(groups, dict) or any(
        not isinstance(name, str)
        or not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
        for name, patterns in groups.items()
    ):
        raise ValueError("PR dependency groups must contain nonempty path lists")
    paths = [path.replace("\\", "/") for path in paths if path]
    force = not paths or bool(CONTROL_PATHS.intersection(paths))
    selected = {}
    for lane, rule in policy.items():
        names = rule.get("dependency-groups", [])
        if not isinstance(names, list) or any(
            not isinstance(name, str) or name not in groups for name in names
        ):
            raise ValueError(f"{lane}: unknown or malformed PR dependency group")
        patterns = list(rule.get("paths", [])) + [
            pattern for name in names for pattern in groups[name]
        ]
        if force:
            selected[lane] = True
            continue
        branch_ok = not rule.get("branches") or matches(base, rule["branches"])
        if rule.get("branches-ignore") and matches(base, rule["branches-ignore"]):
            branch_ok = False
        path_ok = not patterns or any(matches(path, patterns) for path in paths)
        if rule.get("paths-ignore") and all(
            matches(path, rule["paths-ignore"]) for path in paths
        ):
            path_ok = False
        selected[lane] = branch_ok and path_ok
    return selected


def validation_errors(plan: dict, needs: dict, lanes: set[str]) -> list[str]:
    errors = []
    if set(plan) != lanes or any(type(value) is not bool for value in plan.values()):
        errors.append("PR selection inventory is missing, malformed or unexpected")
    if set(needs) != lanes | {"select"}:
        errors.append("PR dependency inventory differs from the reviewed lanes")
    if needs.get("select", {}).get("result") != "success":
        errors.append("PR selection did not succeed")
    for lane in sorted(lanes):
        actual = needs.get(lane, {}).get("result")
        expected = "success" if plan.get(lane) is True else "skipped"
        if actual != expected:
            errors.append(f"{lane}: expected {expected}, got {actual}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    choose = sub.add_parser("select")
    choose.add_argument("paths", type=Path)
    choose.add_argument("--base", required=True)
    choose.add_argument("--output", type=Path, required=True)
    sub.add_parser("check")
    args = parser.parse_args()
    if args.command == "select":
        plan = select(args.paths.read_text(encoding="utf-8").splitlines(), args.base)
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write("plan=" + json.dumps(plan, separators=(",", ":")) + "\n")
        print(json.dumps(plan, indent=2))
        return 0
    lanes = set(json.loads(POLICY.read_text(encoding="utf-8")))
    errors = validation_errors(
        json.loads(os.environ["PR_PLAN"]), json.loads(os.environ["PR_NEEDS"]), lanes
    )
    if errors:
        raise SystemExit("PR validation rejected:\n- " + "\n- ".join(errors))
    print(
        "All selected PR workflows passed; all excluded workflows were deliberately skipped."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
