"""Prove four critical contract tests kill regressions in an isolated Git snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET

COMPONENT = "custom_components/extended_openai_conversation_responses/"
MATRIX = "tests_real_ha/test_entry_point_contract_matrix.py::"


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    anchor: str
    replacement: str
    test: str


MUTATIONS = (
    Mutation(
        "authorization",
        COMPONENT + "ha_actions.py",
        "await async_require_control_permission(hass, entity_ids, context=context)",
        "pass  # sensitivity: omit caller authorization",
        MATRIX + "test_action_routes_share_actual_user_control_boundary[False-native]",
    ),
    Mutation(
        "request-validation",
        COMPONENT + "management_ui.py",
        "validate_routed_request_options(config, entry_data)",
        "pass  # sensitivity: skip effective request validation",
        MATRIX
        + "test_invalid_request_cannot_publish_through_any_setup_writer[hosted-search-api-update]",
    ),
    Mutation(
        "failure-result",
        COMPONENT + "services.py",
        '"successful": error_code is None,',
        '"successful": True,',
        MATRIX
        + "test_failure_semantics_survive_every_conversation_doorway[provider-process]",
    ),
    Mutation(
        "restoration",
        COMPONENT + "quiet_hours_runtime.py",
        "current = _current_volume(self.hass, entity_id)\n                        if current is None:\n                            continue",
        "current = _current_volume(self.hass, entity_id)\n                        if current is None:\n                            controls.pop(entity_id)\n                            continue",
        "tests_real_ha/test_quiet_hours_acceptance.py::test_unavailable_quiet_hours_controls_restore_after_return",
    ),
)


def mutate(source: str, mutation: Mutation) -> str:
    """Fail closed if the reviewed anchor has disappeared or become ambiguous."""
    if source.count(mutation.anchor) != 1:
        raise ValueError(f"{mutation.name}: expected exactly one mutation anchor")
    return source.replace(mutation.anchor, mutation.replacement, 1)


def classify(exit_code: int, report: Path) -> str:
    """Import/fixture/collection errors and timeouts never count as a kill."""
    if not report.exists():
        return "invalid"
    cases = ET.parse(report).getroot().findall(".//testcase")
    if (
        len(cases) != 1
        or cases[0].find("error") is not None
        or cases[0].find("skipped") is not None
    ):
        return "invalid"
    failure = cases[0].find("failure")
    if exit_code == 0 and failure is None:
        return "survived"
    if exit_code == 1 and failure is not None:
        message = failure.get("message", "")
        # pytest assertion failures and "DID NOT RAISE" are expected outcomes.
        if (
            message.startswith("AssertionError")
            or message.startswith("assert ")
            or message.startswith("Failed: DID NOT RAISE")
        ):
            return "killed"
    return "invalid"


def run_case(snapshot: Path, output: Path, test: str, label: str) -> str:
    report = output / f"{label}.xml"
    with (output / f"{label}.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    test,
                    "-q",
                    "--timeout=60",
                    "-p",
                    "no:cacheprovider",
                    f"--junitxml={report}",
                ],
                cwd=snapshot,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "invalid"
    return classify(result.returncode, report)


def campaign(repo: Path, output: Path) -> bool:
    """Archive committed source; never modify the user's checkout."""
    output.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    results = []
    with tempfile.TemporaryDirectory(prefix="eoai-contract-sensitivity-") as temporary:
        scratch = Path(temporary)
        archive, snapshot = scratch / "source.tar", scratch / "snapshot"
        snapshot.mkdir()
        subprocess.run(
            ["git", "archive", "HEAD", "-o", str(archive)], cwd=repo, check=True
        )
        with tarfile.open(archive) as source:
            source.extractall(snapshot, filter="data")
        for mutation in MUTATIONS:
            path = snapshot / mutation.path
            original = path.read_bytes()
            baseline = run_case(
                snapshot, output, mutation.test, mutation.name + "-baseline"
            )
            outcome = "not-run"
            try:
                if baseline == "survived":
                    path.write_text(
                        mutate(original.decode("utf-8"), mutation), encoding="utf-8"
                    )
                    outcome = run_case(
                        snapshot, output, mutation.test, mutation.name + "-mutated"
                    )
            finally:
                path.write_bytes(original)
            results.append(
                {
                    "name": mutation.name,
                    "test": mutation.test,
                    "baseline": baseline,
                    "mutation": outcome,
                }
            )
            print(
                f"{mutation.name}: baseline={baseline}, mutation={outcome}", flush=True
            )
    (output / "results.json").write_text(
        json.dumps({"sha": sha, "results": results}, indent=2) + "\n", encoding="utf-8"
    )
    return all(
        row["baseline"] == "survived" and row["mutation"] == "killed" for row in results
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("contract-sensitivity-artifacts")
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    if not campaign(repo, args.output.resolve()):
        raise SystemExit(
            "Contract sensitivity failed; inspect results.json and per-case logs"
        )


if __name__ == "__main__":
    main()
