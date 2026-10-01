"""Refresh the single reviewed case catalog from collect-only execution ledgers.

The resulting diff requires review; this command never creates skip allowances
or changes semantic minimums. Supply the complete Python and browser catalogs.
"""

import argparse
import json
from pathlib import Path

from execution_contract import CONTRACT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pytest", type=Path, required=True)
    parser.add_argument("--browser", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    policy = json.loads(CONTRACT.read_text())
    ledger = json.loads(args.pytest.read_text())
    if ledger["runner"] != "pytest" or ledger["exit_status"] != 0:
        raise SystemExit("Python collection must complete successfully")
    policy["pytest"] = sorted({case["nodeid"] for case in ledger["cases"]})
    browser = {key: set() for key in ("browser", "browser-firefox", "browser-webkit")}
    for path in args.browser:
        ledger = json.loads(path.read_text())
        if ledger["runner"] != "playwright" or ledger["status"] != "passed":
            raise SystemExit("Browser collection must complete successfully")
        for case in ledger["cases"]:
            node = case["nodeid"]
            if node in policy["fixture_only"]:
                continue
            engine = node.split("::")[1]
            browser["browser" if engine == "chromium" else f"browser-{engine}"].add(
                node
            )
    for engine in ("firefox", "webkit"):
        browser[f"browser-{engine}"].update(
            node.replace("::chromium::", f"::{engine}::")
            for node in browser["browser"]
            if node.startswith("tests_browser/real-ha-multi-tab")
        )
    policy["browser"] = {key: sorted(nodes) for key, nodes in browser.items()}
    CONTRACT.write_text(json.dumps(policy, indent=2) + "\n")


if __name__ == "__main__":
    main()
