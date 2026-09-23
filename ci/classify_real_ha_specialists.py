"""Conservative PR path classifier for the three expensive Real-HA shards.

Unknown backend paths run every specialist. The explicit unrelated set is kept
small so new runtime modules cannot silently lose process-boundary coverage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

COMPONENT = "custom_components/extended_openai_conversation_responses/"
ALL = frozenset("abc")
SPECIALIST_SHARDS = {
    "a": ("process-restart-a",),
    # These cases have isolated tmp_path directories and independent HA children.
    "b": ("process-restart-b-active-request", "process-restart-b-immediate-tool"),
    "c": ("process-restart-c-before-commit", "process-restart-c-after-commit"),
}

# These modules have no request, delayed-tool, Store, or shutdown ownership.
# Broad parallel-safe Real-HA still runs for their backend PRs.
UNRELATED_MODULES = {
    "usage.py",
    "model_catalog.py",
    "model_capabilities.py",
    "frontend_assets.py",
    "frontend_version.py",
    "management_ui.py",
    "management_projections.py",
}


def classify_path(path: str) -> frozenset[str]:
    """Return specialist shards required by one old or new diff path."""
    path = path.replace("\\", "/")
    if path.startswith(("frontend/", COMPONENT + "frontend/", "tests_browser/")):
        return frozenset()
    if path.startswith("tests_real_ha/"):
        name = path.removeprefix("tests_real_ha/")
        if name == "test_delayed_tool_process_restart.py":
            return frozenset("a")
        if name in {
            "test_active_request_shutdown_recovery.py",
            "test_immediate_tool_process_crash.py",
        }:
            return frozenset("b")
        if name == "test_pending_store_write_shutdown_recovery.py":
            return frozenset("c")
        # Shared HA fixtures and harnesses can affect every process boundary.
        if not name.startswith("test_"):
            return ALL
        return frozenset()
    if path.startswith("tests/") and path.endswith(".py"):
        name = Path(path).name
        if name.startswith(("test_usage", "test_model_catalog", "test_management_ui")):
            return frozenset()
        if "delayed_tool" in name:
            return frozenset("a")
        if "temporary_memory" in name or "persistence" in name or "store" in name:
            return frozenset("c")
        if "request" in name or "function" in name or "shutdown" in name:
            return frozenset("b")
        # Unknown unit coverage might share fixtures or follow a new runtime path.
        return ALL
    if path.startswith(COMPONENT):
        name = path.removeprefix(COMPONENT)
        if name in UNRELATED_MODULES:
            return frozenset()
        # Delayed scheduling, retries and restore/maintenance gates.
        if name in {
            "delayed_tools.py", "function_tool_recovery.py", "restore_recovery.py",
            "agent_maintenance.py", "function_execution.py", "agent.py",
        }:
            return frozenset("ab")
        # Provider/request execution, cancellation, tool calls and cleanup.
        if name in {
            "request.py", "provider_loop.py", "openai_compat.py",
            "non_streaming.py", "parallel_tool_execution.py", "tool_exchange.py",
            "function_tool_resolution.py", "function_tool_policy.py",
        } or name.startswith("functions/"):
            return frozenset("b")
        # Temporary memory and Store transaction/atomicity helpers.
        if name == "temporary_memory.py":
            return frozenset("c")
        # Integration lifecycle and shared agent configuration touch all three.
        return ALL
    # Workflow, classifier, dependency and unfamiliar paths fail open. This also
    # covers both sides of a rename because the workflow uses --no-renames.
    return ALL


def classify(paths: list[str], *, pull_request: bool) -> frozenset[str]:
    if not pull_request or not paths:
        return ALL
    required: set[str] = set()
    for path in paths:
        required.update(classify_path(path))
    return frozenset(required)


def matrix_shards(required: frozenset[str]) -> list[str]:
    """Expand logical coverage categories to the independently scheduled jobs."""
    return ["parallel-safe", "stateful-serial"] + [
        shard for category in "abc" if category in required
        for shard in SPECIALIST_SHARDS[category]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [line for line in args.paths_file.read_text().splitlines() if line]
    required = classify(paths, pull_request=True)
    with args.output.open("a", encoding="utf-8") as output:
        for shard in "abc":
            output.write(f"process_restart_{shard}={str(shard in required).lower()}\n")
        shards = matrix_shards(required)
        output.write("shards=" + json.dumps(shards, separators=(",", ":")) + "\n")
    print("Specialist process-restart shards: " + (", ".join(sorted(required)) or "none"))


if __name__ == "__main__":
    main()
