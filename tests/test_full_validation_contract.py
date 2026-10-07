"""Keep expensive native lanes in the immutable full-validation campaign."""

import ast
from pathlib import Path


def test_full_validation_dispatches_both_companion_apps_and_appliance_lanes():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "ci/full_validation.py").read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TEST_WORKFLOWS"
            for target in node.targets
        )
    )
    workflows = [name for name, _ in ast.literal_eval(assignment.value)]
    assert len(workflows) == len(set(workflows))
    assert {
        "android-companion-app.yml",
        "ios-companion-app.yml",
        "haos-supervisor-vm-acceptance.yml",
        "ha-version-upgrade-acceptance.yml",
        "enhanced-stress.yml",
    } <= set(workflows)
    assert all((root / ".github" / "workflows" / name).is_file() for name in workflows)
