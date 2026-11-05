"""Check static browser inventory without launching a browser or running tests."""

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = (
    ("playwright.stress.config.mjs", ()),
    (
        "playwright.config.mjs",
        (
            "tests_browser/retained-mutation-state.spec.mjs",
            "tests_browser/backend-reconnect.spec.mjs",
            "tests_browser/management-lazy-ownership.spec.mjs",
        ),
    ),
)
# Genuine HA entries are checked against executed native evidence by the final
# certificate. The preflight collects only the curated static engine journeys.
ENGINE_NATIVE_FILES = {
    "tests_browser/real-ha-multi-tab.stress.mjs",
    "tests_browser/real-ha-shell.spec.mjs",
}


def inventory_errors(nodes, expected, reviewed):
    actual, expected = Counter(nodes), Counter(expected)
    errors = []
    for label, delta in (
        ("missing", expected - actual),
        ("unexpected", actual - expected),
    ):
        errors.extend(
            f"{label}: {node} (count={count})" for node, count in sorted(delta.items())
        )
    errors.extend(f"unreviewed: {node}" for node in sorted(set(actual) - set(reviewed)))
    return errors


def collect_static(root=ROOT, *, engine=None):
    nodes = []
    plans = (
        (("playwright.cross-browser-nightly.config.mjs", ()),)
        if engine
        else COLLECTIONS
    )
    with TemporaryDirectory(prefix="eoai-browser-collection-") as temporary:
        for index, (config, specs) in enumerate(plans):
            output = Path(temporary) / str(index)
            environment = {
                **os.environ,
                "ENHANCED_EXECUTION_EVIDENCE": "1",
                "STRESS_ARTIFACT_DIR": str(output),
            }
            for key in (
                "CROSS_BROWSER_ENGINE",
                "CROSS_BROWSER_NIGHTLY",
                "REAL_HA_BROWSER_PROFILE",
            ):
                environment.pop(key, None)
            if engine:
                environment["CROSS_BROWSER_ENGINE"] = engine
                environment["CROSS_BROWSER_NIGHTLY"] = "1"
                environment["REAL_HA_BROWSER_PROFILE"] = engine
            result = subprocess.run(
                [
                    "node",
                    "node_modules/@playwright/test/cli.js",
                    "test",
                    "--list",
                    f"--config={config}",
                    "--reporter=./ci/playwright_execution.mjs",
                    *specs,
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Browser collection failed:\n{result.stdout}{result.stderr}"
                )
            ledgers = list(output.glob("execution-playwright-*.json"))
            if len(ledgers) != 1:
                raise RuntimeError(
                    "Collection must produce exactly one isolated ledger"
                )
            ledger = json.loads(ledgers[0].read_text(encoding="utf-8"))
            if (
                ledger["runner"] != "playwright"
                or ledger["status"] != "passed"
                or not ledger["cases"]
            ):
                raise RuntimeError("Browser collection did not complete")
            if any(
                not case["collected"] or case["executed"] for case in ledger["cases"]
            ):
                raise RuntimeError(
                    "Collect-only check must never execute a browser test"
                )
            nodes.extend(case["nodeid"] for case in ledger["cases"])
    return nodes


def main():
    path = ROOT / "tests_stress"
    inventory = json.loads(
        (path / "browser_partition_contract.json").read_text(encoding="utf-8")
    )
    policy = json.loads(
        (path / "nightly_execution_contract.json").read_text(encoding="utf-8")
    )
    nodes = collect_static()
    errors = inventory_errors(
        nodes,
        inventory["partitions"]["static"],
        set(policy["browser"]["browser"]) | set(policy["fixture_only"]),
    )
    counts = {"chromium": len(nodes)}
    for engine in ("firefox", "webkit"):
        reviewed = [
            node
            for node in policy["browser"][f"browser-{engine}"]
            if node.split("::")[0].endswith(".mjs")
            and node.split("::")[0] not in ENGINE_NATIVE_FILES
        ]
        observed = collect_static(engine=engine)
        errors.extend(
            f"{engine}: {error}"
            for error in inventory_errors(observed, reviewed, reviewed)
        )
        counts[engine] = len(observed)
    if errors:
        raise SystemExit(
            "Browser collection differs from reviewed inventory:\n" + "\n".join(errors)
        )
    print(f"Browser collections match reviewed executions {counts}; no tests ran.")


if __name__ == "__main__":
    main()
