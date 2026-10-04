"""Sensitivity must distinguish assertion kills from broken infrastructure."""

import pytest

from scripts.run_contract_sensitivity import MUTATIONS, classify, mutate


@pytest.mark.parametrize(
    "exit_code,detail,expected",
    [
        (0, "", "survived"),
        (1, '<failure message="assert False"/>', "killed"),
        (1, '<failure message="AssertionError: denied"/>', "killed"),
        (1, '<failure message="Failed: DID NOT RAISE exception"/>', "killed"),
        (1, '<failure message="NameError: broken mutation"/>', "invalid"),
        (1, '<failure message="Failed: Timeout"/>', "invalid"),
        (1, '<error message="ImportError"/>', "invalid"),
        (0, '<skipped message="unsupported"/>', "invalid"),
        (2, "", "invalid"),
    ],
)
def test_only_real_assertion_failures_count_as_kills(
    tmp_path, exit_code, detail, expected
):
    report = tmp_path / "report.xml"
    report.write_text(
        f"<testsuites><testsuite><testcase>{detail}</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    assert classify(exit_code, report) == expected


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda item: item.name)
def test_mutation_anchor_is_unique_and_checked_against_current_source(mutation):
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / mutation.path).read_text(
        encoding="utf-8"
    )
    changed = mutate(source, mutation)
    assert changed != source
    assert mutation.anchor not in changed
    with pytest.raises(ValueError):
        mutate("no anchor", mutation)
    with pytest.raises(ValueError):
        mutate(source + mutation.anchor, mutation)
