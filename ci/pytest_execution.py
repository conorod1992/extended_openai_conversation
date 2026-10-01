"""Opt-in pytest execution ledger for ordinary and stress acceptance cases.

Only node IDs, phase outcomes and collection status are saved; no request bodies,
locals, failure messages or skip reasons enter the certification artifact.
"""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from ci.enhanced_evidence import final_pytest_outcome, write_json


class ExecutionLedger:
    def __init__(self):
        self.collected = set()
        self.deselected = set()
        self.reports = {}

    def pytest_itemcollected(self, item):
        self.collected.add(item.nodeid)

    def pytest_deselected(self, items):
        self.deselected.update(item.nodeid for item in items)

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, {})[report.when] = report

    def pytest_sessionfinish(self, session, exitstatus):
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
            root / f"execution-pytest-{uuid.uuid4().hex}.json",
            {
                "execution_schema": "eoai-test-execution/v1",
                "runner": "pytest",
                "exit_status": int(exitstatus),
                "cases": cases,
            },
        )


def pytest_configure(config):
    if os.environ.get("ENHANCED_EXECUTION_EVIDENCE") == "1":
        config.pluginmanager.register(ExecutionLedger(), "enhanced-execution-ledger")
