"""Campaign ownership changes must preserve cases and semantic obligations."""

import ast
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

import yaml

from ci.execution_contract import CONTRACT, expected_cases, selected
from ci.pr_validation import select
from ci.validation_inventory import CERTIFICATION_WORKFLOW, TEST_WORKFLOWS


def test_expanded_provider_parameter_cases_match_the_reviewed_inventory():
    """Parameter expansion must update exact case obligations, not just function names."""
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    functions = {
        "tests/test_ai_task_replay_lifecycle.py": {
            "test_successful_ai_tasks_release_receipts_before_retention_eviction",
            "test_failed_ai_task_continuations_retain_effects_until_acknowledged",
            "test_structured_output_failure_follows_provider_acknowledgement",
        },
        "tests/test_openai_sdk_multicall_wire.py": {
            "test_two_sdk_wire_calls_keep_ids_arguments_and_outputs_paired",
        },
    }
    aliases = {
        "API_MODE_RESPONSES": "responses",
        "API_MODE_CHAT_COMPLETIONS": "chat_completions",
    }
    for path, names in functions.items():
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for function in tree.body:
            if not isinstance(function, ast.AsyncFunctionDef) or function.name not in names:
                continue
            domains = []
            for decorator in reversed(function.decorator_list):
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "parametrize"
                ):
                    continue
                domains.append([
                    aliases[value.id] if isinstance(value, ast.Name) else ast.literal_eval(value)
                    for value in decorator.args[1].elts
                ])
            prefix = f"{path}::{function.name}["
            collected = {
                prefix + "-".join(map(str, values)) + "]"
                for values in product(*domains)
            }
            assert collected == {node for node in policy["pytest"] if node.startswith(prefix)}


def test_owned_files_have_exactly_one_same_environment_campaign():
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    owners = json.loads(Path("ci/campaign_owners.json").read_text(encoding="utf-8"))
    for file, owner in owners.items():
        campaigns = {
            campaign
            for campaign, selectors in policy["selections"].items()
            if campaign
            not in {
                "minimum-ha-features",
                "public-version-journeys",
                "lifecycle-matrix",
            }
            and any(selector.split("::")[0] == file for selector in selectors)
        }
        assert campaigns == {owner}, file
        assert any(selected(node, file) for node in expected_cases(policy, owner)), file


def test_programme_union_and_all_semantic_minimums_are_preserved():
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    baseline = json.loads(
        Path("tests_stress/programme_coverage_contract.json").read_text(
            encoding="utf-8"
        )
    )
    nodes = set().union(*(expected_cases(policy, c) for c in policy["selections"]))

    # Retain the historical programme digest while requiring every reviewed
    # sequential replacement in the Functions campaign.
    assist = "tests_stress/test_function_provider_wire_remaining.py::"
    budget = (
        "tests_stress/test_function_provider_wire.py::"
        "test_public_budget_loading_multicall_and_completed_replay"
    )
    replaced_cases = {
        f"{assist}test_public_assist_multicall_dispatch_concurrency[{old}-{api}]": (
            f"{assist}test_public_assist_multicall_dispatch_order[{new}-{api}]"
        )
        for old, new in (
            ("mixed-serial", "mixed-serial"),
            ("parent-cancel", "parent-cancel"),
            ("safe-overlap", "read-only-serial"),
            ("sibling-failure", "sibling-failure"),
        )
        for api in ("chat_completions", "responses")
    }
    replaced_cases[f"{budget}[safe-atomic]"] = f"{budget}[read-only-prefix]"
    assert set(replaced_cases.values()) <= expected_cases(policy, "functions")
    assert set(replaced_cases).isdisjoint(nodes)
    nodes.difference_update(replaced_cases.values())
    nodes.update(replaced_cases)
    assert (
        sha256("\n".join(sorted(nodes)).encode()).hexdigest()
        == baseline["pytest_union_sha256"]
    )
    metrics = {}
    for minimums in policy["minimums"].values():
        for name, value in minimums.items():
            metrics[name] = max(value, metrics.get(name, 0))

    # Compare the two replaced obligations at their original minimum counts.
    for old, new in (
        ("assist_safe_overlap", "assist_read_only_serial"),
        ("budget_atomic_cases", "budget_read_only_prefix_cases"),
    ):
        assert old not in metrics
        assert policy["minimums"]["functions"][new] == baseline["minimums"][old]
        metrics[old] = metrics.pop(new)
    assert metrics == baseline["minimums"]


def test_generator_proofs_are_change_triggered_without_running_for_unrelated_changes():
    assert not select(["docs/development/example.md"], "develop")["generator-proofs"]
    assert not select(
        ["custom_components/extended_openai_conversation_responses/intercom.py"],
        "develop",
    )["generator-proofs"]
    for path in (
        "tests_stress/generated_valid_states.py",
        "custom_components/extended_openai_conversation_responses/agent_config.py",
        "custom_components/extended_openai_conversation_responses/model_catalog.json",
        "custom_components/extended_openai_conversation_responses/model_capabilities.py",
        "custom_components/extended_openai_conversation_responses/request.py",
    ):
        assert select([path], "develop")["generator-proofs"], path


def test_collector_listens_to_every_dispatched_workflow_and_queues_without_a_runner():
    root = Path(".github/workflows")
    data = yaml.safe_load(
        (root / "full-validation-collect.yml").read_text(encoding="utf-8")
    )
    triggers = data.get("on", data.get(True))
    expected = {"Full validation"} | {
        yaml.safe_load((root / name).read_text(encoding="utf-8"))["name"]
        for name in [*(name for name, _ in TEST_WORKFLOWS), CERTIFICATION_WORKFLOW]
    }
    assert set(triggers["workflow_run"]["workflows"]) == expected
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert data["concurrency"] == {
        "group": "full-validation-collector",
        "cancel-in-progress": False,
        "queue": "max",
    }
