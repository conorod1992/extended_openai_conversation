"""Protect campaign isolation and truthful mutation result reporting."""

from pathlib import Path
import tomllib

import pytest

from scripts.run_mutation_campaign import (
    campaign_config,
    configured_campaign,
    require_complete,
    selected_results,
)

PROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


@pytest.mark.parametrize(
    "campaign,modules,tests",
    [
        (
            "function-tools",
            {
                "function_call_budget",
                "function_tool_resolution",
                "function_tool_recovery",
                "parallel_tool_execution",
            },
            8,
        ),
        ("guest-security", {"guest_mode"}, 1),
        ("ha-permissions", {"ha_permissions"}, 1),
        ("request-rules", {"request_rules"}, 1),
        ("function-groups", {"function_groups"}, 1),
    ],
)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_campaign_isolation_and_restoration_after_failure(
    tmp_path, campaign, modules, tests, newline
):
    original = PROJECT.read_text(encoding="utf-8").replace("\n", newline).encode()
    path = tmp_path / "pyproject.toml"
    path.write_bytes(original)
    config = tomllib.loads(original.decode())["tool"]
    with (
        pytest.raises(RuntimeError, match="tool failed"),
        configured_campaign(path, campaign_config(config, campaign)),
    ):
        active = tomllib.loads(path.read_text(encoding="utf-8"))["tool"]
        assert {Path(p).stem for p in active["mutmut"]["only_mutate"]} == modules
        focused = active["mutmut"]["pytest_add_cli_args_test_selection"]
        assert len(focused) == tests
        assert all((PROJECT.parent / test).is_file() for test in focused)
        assert active["pytest"] == config["pytest"]
        assert active["mutation-campaigns"] == config["mutation-campaigns"]
        raise RuntimeError("tool failed")
    assert path.read_bytes() == original


def test_unmatched_override_cannot_report_success():
    with pytest.raises(ValueError, match="No generated mutants"):
        selected_results("module.x_target__mutmut_1: killed", ["typo.*"])


def test_every_selector_must_match():
    with pytest.raises(ValueError, match="missing"):
        selected_results("module.x_target__mutmut_1: killed", ["module.*", "missing.*"])


def test_override_reports_only_selected_mutants_and_accepts_reviewable_survivors():
    results = selected_results(
        "module.x_target__mutmut_1: killed\n"
        "module.x_target__mutmut_2: survived\n"
        "module.x_other__mutmut_1: not checked",
        ["module.x_target__mutmut_*"],
    )
    assert results == {
        "module.x_target__mutmut_1": "killed",
        "module.x_target__mutmut_2": "survived",
    }
    require_complete(results)


def test_function_tool_default_accepts_generated_modules_without_decorated_budget():
    config = tomllib.loads(PROJECT.read_text(encoding="utf-8"))["tool"]
    selectors = config["mutation-campaigns"]["function-tools"]["selectors"]
    # Mutmut 3.7.0 skips the decorated budget class; all generated mutants in
    # the module allowlist must still run, as with the original unfiltered CLI.
    output = "\n".join(
        f"custom_components.extended_openai_conversation_responses.{module}.x_example__mutmut_1: killed"
        for module in (
            "function_tool_resolution",
            "function_tool_recovery",
            "parallel_tool_execution",
        )
    )
    assert len(selected_results(output, selectors)) == 3


@pytest.mark.parametrize(
    "status",
    [
        "not checked",
        "no tests",
        "timeout",
        "suspicious",
        "segfault",
        "interrupted",
        "unknown",
    ],
)
def test_incomplete_mutation_is_not_success(status):
    with pytest.raises(ValueError, match="unresolved"):
        require_complete({"module.x_target__mutmut_1": status})
