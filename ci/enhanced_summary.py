"""Turn deterministic enhanced-test traces into a useful GitHub Step Summary."""

from collections import Counter
import json
import os
from pathlib import Path
import sys

COUNT_METRICS = {
    "public_turns",
    "provider_requests",
    "embedding_provider_requests",
    "actual_tool_executions",
    "actual_function_executions",
    "local_function_executions",
    "native_function_executions",
    "template_function_executions",
    "script_function_executions",
    "ha_service_calls",
    "guest_end_to_end_combinations",
    "private_context_probes",
    "rollback_phases",
    "backup_chunks_transferred",
    "transfer_sessions",
    "historical_fixtures",
    "browser_creates",
    "browser_edits",
    "browser_deletes",
    "multi_tab_conflicts",
    "stale_responses",
    "reconnect_cycles",
    "quiet_hours_transitions",
    "chaos_operations",
    "process_terminations",
    "setup_flows",
}


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
    totals: Counter[str] = Counter()
    if not files:
        lines += [
            "Selected Real HA tests report their assertions in the pytest log; no enhanced operation trace was produced.",
            "",
        ]
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        operations = data.get("operations", [])
        counts = Counter(item.get("operation", "unknown") for item in operations)
        lines += [
            f"**{data.get('test', path.stem)}**",
            "",
            f"Evidence layer: **{'browser' if path.stem.startswith('browser-') else layer_for(data.get('test', path.stem), operations)}** · Trace events: {len(operations)}",
            "",
        ]
        if counts:
            lines += ["| Operation | Count |", "| --- | ---: |"]
            lines += [f"| {name} | {count} |" for name, count in sorted(counts.items())]
            lines.append("")
        for item in operations:
            if item.get("operation") == "summary":
                for key in COUNT_METRICS:
                    value = item.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        totals[key] += value
                details = ", ".join(
                    f"{key}={value}"
                    for key, value in item.items()
                    if key not in {"operation", "number"}
                )
                lines += [f"Measured: {details}", ""]
        if "maxNodes" in data:
            totals["browser_creates"] += int(data.get("creates", 0))
            totals["browser_edits"] += int(data.get("edits", 0))
            totals["browser_deletes"] += int(data.get("deletes", 0))
            lines += [
                f"Browser transitions: {data['count']}; creates: {data.get('creates', 0)}; edits: {data.get('edits', 0)}; deletes: {data.get('deletes', 0)}; maximum observed panel DOM nodes: {data['maxNodes']}",
                "",
            ]
        if "cycles" in data:
            totals["reconnect_cycles"] += int(data["cycles"])
            lines += [
                f"Reconnect cycles: {data['cycles']}; baseline backend calls per forced read: {data.get('baselineCalls')}",
                "",
            ]
    if totals:
        lines += ["**Measured totals**", "", "| Metric | Count |", "| --- | ---: |"]
        lines += [f"| {key} | {value} |" for key, value in sorted(totals.items())]
        lines.append("")
    summary = "\n".join(lines)
    print(summary)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary + "\n")


if __name__ == "__main__":
    main()
