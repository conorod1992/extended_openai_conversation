"""Turn deterministic enhanced-test traces into a useful GitHub Step Summary."""

from collections import Counter
import json
import os
from pathlib import Path
import sys


def layer_for(test: str, operations: list[dict]) -> str:
    """Classify evidence by its deepest exercised boundary, not by test count."""
    explicit = next(
        (item.get("layer") for item in operations if item.get("layer")), None
    )
    if explicit:
        return str(explicit)
    if "browser" in test or test.endswith(".stress.mjs"):
        return "browser"
    if "function_groups_state_machine" in test or "request_rules_matrix" in test:
        return "model-level"
    return "real-ha"


def main() -> None:
    folder = Path(sys.argv[1])
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    campaign = os.environ.get("STRESS_CAMPAIGN", "unknown")
    seed = os.environ.get("STRESS_SEED", "unknown")
    intensity = os.environ.get("STRESS_INTENSITY", "normal")
    lines = [
        f"### Enhanced acceptance: {campaign}",
        "",
        f"Seed: `{seed}` · Intensity: `{intensity}`",
        "",
    ]
    if not files:
        lines += [
            "No trace files were produced. Inspect the test log and failure artifacts.",
            "",
        ]
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        operations = data.get("operations", [])
        counts = Counter(item.get("operation", "unknown") for item in operations)
        lines += [
            f"**{data.get('test', path.stem)}**",
            "",
            f"Evidence layer: **{layer_for(data.get('test', path.stem), operations)}** · Trace events: {len(operations)}",
            "",
        ]
        if counts:
            lines += ["| Operation | Count |", "| --- | ---: |"]
            lines += [f"| {name} | {count} |" for name, count in sorted(counts.items())]
            lines.append("")
        for item in operations:
            if item.get("operation") == "summary":
                details = ", ".join(
                    f"{key}={value}"
                    for key, value in item.items()
                    if key not in {"operation", "number"}
                )
                lines += [f"Measured: {details}", ""]
        if "maxNodes" in data:
            lines += [
                f"Browser transitions: {data['count']}; successful backend mutations: {data.get('mutations', 0)}; maximum observed panel DOM nodes: {data['maxNodes']}",
                "",
            ]
    summary = "\n".join(lines)
    print(summary)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary + "\n")


if __name__ == "__main__":
    main()
