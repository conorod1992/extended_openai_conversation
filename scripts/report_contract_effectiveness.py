"""Summarize existing contract-sensitivity outcomes without counting broken tests as kills.

Use the reviewed critical probes as an enforcement baseline. New exploratory
challenges may be tracked separately and must not inflate that baseline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = frozenset(
    {"authorization", "request-validation", "failure-result", "restoration"}
)


def summarize(payload: dict) -> dict:
    records = payload.get("results", [])
    names = [item["name"] for item in records]
    if len(names) != len(set(names)):
        raise ValueError("duplicate challenge names")
    required_missing = sorted(REQUIRED - set(names))
    categories = {"detected": [], "missed": [], "inconclusive": []}
    for row in records:
        name = row["name"]
        baseline, mutation = row.get("baseline"), row.get("mutation")
        if baseline != "survived" or mutation not in {"killed", "survived"}:
            categories["inconclusive"].append(name)
        elif mutation == "killed":
            categories["detected"].append(name)
        else:
            categories["missed"].append(name)
    for category in categories.values():
        category.sort()
    failing_required = sorted(
        REQUIRED - set(categories["detected"])
    )
    return {
        "sha": payload.get("sha"),
        **categories,
        "required_missing": required_missing,
        "required_regressions": failing_required,
        "required_pass": not failing_required,
        "conclusive": len(categories["detected"]) + len(categories["missed"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = summarize(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not summary["required_pass"]:
        raise SystemExit("Previously established critical contract detection regressed")


if __name__ == "__main__":
    main()
