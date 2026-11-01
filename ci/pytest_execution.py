"""Opt-in pytest execution ledger for ordinary and stress acceptance cases.

Only node IDs, phase outcomes and collection status are saved; no request bodies,
locals, failure messages or skip reasons enter the certification artifact.
"""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from ci.enhanced_evidence import envelope, final_pytest_outcome, write_json


class ExecutionLedger:
    def __init__(self):
        self.collected = set()
        self.deselected = set()
        self.reports = {}

    def pytest_itemcollected(self, item):
        self.collected.add(item.nodeid)

    def pytest_deselected(self, items):
        self.deselected.update(item.nodeid for item in items)

    def pytest_collection_modifyitems(self, items):
        partition = os.environ.get("EOAI_LIFETIME_PARTITION", "")
        if not partition:
            return
        if (
            partition not in {"reload", "uninterrupted"}
            or os.environ.get("STRESS_CAMPAIGN") != "long-lifetime"
        ):
            raise ValueError("Invalid isolated lifetime partition")
        companion = (
            "tests_stress/test_process_resource_soak.py::"
            "test_booted_process_uninterrupted_runtime_survives_populated_lifetime"
        )
        assigned = [
            item
            for item in items
            if (item.nodeid == companion) == (partition == "uninterrupted")
        ]
        # Assignment is not a skipped/deselected test. The final certification
        # checks the complete, disjoint union against the independent inventory.
        removed = {item.nodeid for item in items} - {item.nodeid for item in assigned}
        self.collected.difference_update(removed)
        items[:] = assigned

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, {})[report.when] = report

    def pytest_sessionfinish(self, session, exitstatus):
        execution_id = uuid.uuid4().hex
        cases = []
        for node in sorted(self.collected):
            reports = self.reports.get(node, {})
            cases.append(
                {
                    "nodeid": node,
                    "collected": True,
                    "executed": bool(reports),
                    "outcome": "deselected"
                    if node in self.deselected
                    else final_pytest_outcome(reports),
                    "phases": {name: part.outcome for name, part in reports.items()},
                }
            )
        root = Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
        write_json(
            root / f"execution-pytest-{execution_id}.json",
            {
                **envelope(),
                "execution_id": execution_id,
                "execution_schema": "eoai-test-execution/v1",
                "runner": "pytest",
                "exit_status": int(exitstatus),
                "cases": cases,
            },
        )


def pytest_configure(config):
    if os.environ.get("ENHANCED_EXECUTION_EVIDENCE") == "1":
        config.pluginmanager.register(ExecutionLedger(), "enhanced-execution-ledger")
