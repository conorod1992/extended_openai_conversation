"""Validate all 25 defect challenges and the two distinct outcome lanes."""
from pathlib import Path
import py_compile
import tempfile

import pytest

from scripts.defect_challenges import EXPLORATORY
from scripts.run_contract_sensitivity import mutate
from scripts.run_defect_benchmark import all_challenges, classify_suite, coverage_state, mutated_line


@pytest.mark.parametrize("challenge", all_challenges(), ids=lambda x: x.name)
def test_every_challenge_has_unique_compilable_injection(challenge):
    root = Path(__file__).resolve().parent.parent
    source = (root / challenge.path).read_text(encoding="utf-8")
    changed = mutate(source, challenge)
    assert changed != source
    assert challenge.dedicated != challenge.general
    # Injections must remain syntactically valid so import errors cannot masquerade
    # as detection. Do not execute the altered production module in this test.
    compile(changed, str(root / challenge.path), "exec")


def _xml(path, contents):
    path.write_text(
        "<testsuites><testsuite>" + contents + "</testsuite></testsuites>",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "xml,code,baseline,expected",
    [
        ('<testcase name="ok"/>', 0, True, "pass"),
        ('<testcase name="ok"/>', 0, False, "missed"),
        ('<testcase name="bad"><failure message="AssertionError: wrong">AssertionError</failure></testcase>', 1, False, "detected"),
        ('<testcase name="bad"><failure message="NameError: bad">NameError</failure></testcase>', 1, False, "invalid"),
        ('<testcase name="bad"><error message="ImportError"/></testcase>', 1, False, "invalid"),
        ('<testcase name="skipped"><skipped/></testcase>', 0, False, "invalid"),
        ('<testcase name="bad"><failure message="AssertionError: wrong">AssertionError</failure></testcase>', 1, True, "invalid"),
    ],
)
def test_benchmark_classifies_only_valid_assertion_failures(tmp_path, xml, code, baseline, expected):
    output = tmp_path / "test.xml"
    _xml(output, xml)
    assert classify_suite(code, output, baseline=baseline) == expected


def test_benchmark_has_25_distinct_cross_subsystem_challenges():
    cases = all_challenges()
    assert len(cases) == 25
    assert len({item.name for item in cases}) == 25
    assert sum(item.mandatory for item in cases) == 4
    assert len(EXPLORATORY) == 21
    assert len({item.family for item in cases}) >= 7

def test_general_reachability_distinguishes_misses_from_unexecuted_code(tmp_path):
    report = tmp_path / "cov.json"
    target = "custom_components/extended_openai_conversation_responses/example.py"
    report.write_text(
        __import__("json").dumps({"files": {target: {"executed_lines": [7, 8, 11]}}}),
        encoding="utf-8",
    )
    assert coverage_state(report, target, 8) == "executed"
    assert coverage_state(report, target, 9) == "not-executed"
    assert coverage_state(report, "other/never_imported.py", 3) == "not-executed"
    assert coverage_state(report, target, 0) == "unknown"
    assert coverage_state(tmp_path / "missing.json", target, 8) == "unknown"


def test_mutated_line_requires_a_single_line_difference():
    from scripts.defect_challenges import Challenge
    original = "a = 1\\nb = 2\\n"
    one = Challenge("single", "test", "x.py", "a = 1", "a = 2", "t", "g")
    many = Challenge("multiple", "test", "x.py", "a = 1\\nb = 2", "a = 3\\nb = 4", "t", "g")
    assert mutated_line(original, one) == 1
    assert mutated_line(original, many) == 0
