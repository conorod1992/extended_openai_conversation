"""Validate all 25 defect challenges and the two distinct outcome lanes."""
from pathlib import Path
import py_compile
import tempfile

import pytest

from scripts.defect_challenges import EXPLORATORY
from scripts.run_contract_sensitivity import mutate
from scripts.run_defect_benchmark import all_challenges, classify_suite


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
