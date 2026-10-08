"""Two-lane historical defect benchmark, using disposable git archives.

Dedicated lane asks whether the existing targeted tests detect a defect.
General lane runs a separate independently chosen acceptance module; it never
includes the dedicated test selection. A general miss is reported, not hidden.
The four established critical tests remain enforced by run_contract_sensitivity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET

from scripts.defect_challenges import Challenge, EXPLORATORY
from scripts.run_contract_sensitivity import MUTATIONS, mutate


def all_challenges():
    original = [
        Challenge(
            name=m.name,
            family="established-critical",
            path=m.path,
            anchor=m.anchor,
            replacement=m.replacement,
            dedicated=m.test,
            general="tests_real_ha/test_cross_feature_acceptance.py",
            mandatory=True,
        )
        for m in MUTATIONS
    ]
    return tuple(original) + EXPLORATORY


def classify_suite(code: int | None, report: Path, *, baseline: bool) -> str:
    """Count assertion failures only; crashes, collection failures, timeouts invalid."""
    if code is None or not report.exists():
        return "invalid"
    try:
        root = ET.parse(report).getroot()
    except ET.ParseError:
        return "invalid"
    cases = root.findall(".//testcase")
    if not cases:
        return "invalid"
    if any(row.find("error") is not None for row in cases):
        return "invalid"
    failures = [row.find("failure") for row in cases]
    failures = [item for item in failures if item is not None]
    run_count = sum(row.find("skipped") is None for row in cases)
    if not run_count:
        return "invalid"
    if baseline:
        return "pass" if code == 0 and not failures else "invalid"
    if code == 0 and not failures:
        return "missed"
    if code == 1 and failures and all(
        (f.get("message", "").startswith(("AssertionError", "assert ", "Failed: DID NOT RAISE"))
         or "AssertionError" in (f.text or ""))
        and "Timeout" not in f.get("message", "")
        for f in failures
    ):
        return "detected"
    return "invalid"


def mutated_line(source: str, challenge: Challenge) -> int:
    """Locate a unique changed statement, rejecting ambiguous mutations."""
    before = source.splitlines()
    after = mutate(source, challenge).splitlines()
    if len(before) != len(after):
        # Multi-line injections are still valid but cannot safely be assessed
        # with a single line-coverage marker.
        return 0
    changed = [index + 1 for index, (old, new) in enumerate(zip(before, after)) if old != new]
    return changed[0] if len(changed) == 1 else 0


def coverage_state(report: Path, path: str, line: int) -> str:
    """Report whether pytest reached the defective statement at all."""
    if not line or not report.is_file():
        return "unknown"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        for filename, item in data.get("files", {}).items():
            if filename.replace("\\\\", "/").endswith(path):
                return "executed" if line in item.get("executed_lines", []) else "not-executed"
    except (ValueError, OSError, TypeError):
        pass
    return "unknown"


def execute(snapshot: Path, output: Path, label: str, selector: str, baseline: bool, timeout: int,
            coverage_path: str | None = None) -> tuple[str, str]:
    report = output / f"{label}.xml"
    log_path = output / f"{label}.log"
    cov_report = output / f"{label}-coverage.json"
    command = [
        sys.executable, "-m", "pytest", "-q",
        "--tb=short", "--timeout=60", "-p", "no:cacheprovider",
        f"--junitxml={report}",
    ]
    if coverage_path:
        command.extend((
            f"--cov={coverage_path}", f"--cov-report=json:{cov_report}",
            "--cov-fail-under=0",
            "--cov-branch",
        ))
    command.append(selector)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(
                command, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = None
    return classify_suite(code, report, baseline=baseline), str(cov_report)


def evaluate(snapshot: Path, output: Path, case: Challenge, lane: str, timeout: int) -> dict:
    source_path = snapshot / case.path
    original = source_path.read_text(encoding="utf-8")
    selector = case.dedicated if lane == "dedicated" else case.general
    label = f"{case.name}-{lane}"
    try:
        mutated = mutate(original, case)
    except ValueError as error:
        return {"baseline": "not-run", "mutation": "invalid", "reason": str(error)}
    target_line = mutated_line(original, case)
    baseline, _ = execute(snapshot, output, label + "-baseline", selector, True, timeout)
    outcome = "not-run"
    reach = "unknown"
    if baseline == "pass":
        try:
            source_path.write_text(mutated, encoding="utf-8")
            outcome, report = execute(
                snapshot, output, label + "-mutation", selector, False, timeout,
                coverage_path=case.path if lane == "general" else None,
            )
            if lane == "general":
                reach = coverage_state(Path(report), case.path, target_line)
                if outcome == "missed" and reach == "not-executed":
                    outcome = "not-exercised"
        finally:
            source_path.write_text(original, encoding="utf-8")
    result = {"baseline": baseline, "mutation": outcome}
    if lane == "general":
        result.update({"reach": reach, "changed_line": target_line})
    return result


def run(repo: Path, output: Path, names: set[str] | None, lanes: tuple[str, ...], timeout: int) -> dict:
    challenges = all_challenges()
    if len(challenges) != 25 or len({c.name for c in challenges}) != 25:
        raise ValueError("defect benchmark must contain exactly 25 unique cases")
    if names and not names <= {case.name for case in challenges}:
        raise ValueError("unknown challenge IDs: " + ", ".join(sorted(names - {c.name for c in challenges})))
    output.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    results = []
    with tempfile.TemporaryDirectory(prefix="eoai-defect-benchmark-") as tmp:
        snapshot = Path(tmp) / "snapshot"
        snapshot.mkdir()
        archive = Path(tmp) / "source.tar"
        subprocess.run(["git", "archive", "HEAD", "-o", str(archive)], cwd=repo, check=True)
        with tarfile.open(archive) as archive_file:
            archive_file.extractall(snapshot, filter="data")
        for case in challenges:
            if names and case.name not in names:
                continue
            row = {"name": case.name, "family": case.family, "mandatory": case.mandatory}
            for lane in lanes:
                row[lane] = evaluate(snapshot, output, case, lane, timeout)
            results.append(row)
            print(json.dumps(row), flush=True)
            (output / "results.json").write_text(json.dumps({"sha": sha, "results": results}, indent=2) + "\n", encoding="utf-8")
    summary = {}
    for lane in lanes:
        counts = {}
        for row in results:
            outcome = row[lane]["mutation"]
            counts[outcome] = counts.get(outcome, 0) + 1
        summary[lane] = counts
    evidence = {"sha": sha, "results": results, "summary": summary}
    (output / "results.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("defect-benchmark-artifacts"))
    parser.add_argument("--case", action="append", default=[], help="Run only these named challenges")
    parser.add_argument("--lane", choices=("both", "dedicated", "general"), default="both")
    parser.add_argument("--timeout", type=int, default=240, help="Per pytest subprocess budget")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    catalog = all_challenges()
    if args.list:
        print(json.dumps([{"name": c.name, "family": c.family, "mandatory": c.mandatory, "dedicated": c.dedicated, "general": c.general} for c in catalog], indent=2))
        return
    lanes = ("dedicated", "general") if args.lane == "both" else (args.lane,)
    result = run(Path(__file__).resolve().parent.parent, args.output.resolve(), set(args.case) or None, lanes, args.timeout)
    print("Defect challenges evaluated:", len(result["results"]))
    print("Outcome summary:", json.dumps(result["summary"], sort_keys=True))
    print("Unreached injections are classified separately from genuine observed misses; exploratory outcomes do not fail CI")


if __name__ == "__main__":
    main()
