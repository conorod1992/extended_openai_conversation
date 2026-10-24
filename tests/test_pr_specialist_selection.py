"""Specialist PR lanes follow production dependencies, not just their workflows."""

import json

import pytest

from ci.pr_validation import main, select

COMPONENT = "custom_components/extended_openai_conversation_responses/"
SKEW = "frontend-backend-version-skew"
UPGRADE = "ha-version-upgrade-acceptance"
HACS = "hacs-install-update-acceptance"
IPV6 = "ipv6-only-networking-acceptance"
BROWSER = "ha-browser-compatibility"
SPECIALISTS = {SKEW, UPGRADE, HACS, IPV6, BROWSER}


@pytest.mark.parametrize(
    "path,expected",
    [
        (COMPONENT + "frontend/management-panel.js", {SKEW, HACS, BROWSER}),
        (COMPONENT + "frontend/dist/assets/example.js", {SKEW, HACS, BROWSER}),
        (COMPONENT + "frontend_assets.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "management_ui.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "agent_config.py", SPECIALISTS),
        (COMPONENT + "memory.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "migration_schema.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "strict_store.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "entity.py", {UPGRADE, HACS, IPV6, BROWSER}),
        (COMPONENT + "helpers.py", {UPGRADE, HACS, IPV6, BROWSER}),
        (COMPONENT + "functions/web.py", SPECIALISTS),
        (COMPONENT + "functions/custom/example.py", {SKEW, UPGRADE, HACS, BROWSER}),
        (COMPONENT + "manifest.json", SPECIALISTS),
        ("hacs.json", {HACS, UPGRADE, BROWSER}),
        ("scripts/set_release_version.py", {HACS}),
        ("frontend/vite.config.js", {SKEW, HACS, BROWSER}),
        ("requirements_test.txt", SPECIALISTS),
        ("tests_real_ha/conftest.py", SPECIALISTS),
    ],
)
def test_specialists_follow_production_dependencies(path, expected):
    plan = select([path], "develop")
    assert {lane for lane in SPECIALISTS if plan[lane]} == expected


@pytest.mark.parametrize(
    "path,lane",
    [
        ("tests_real_ha/test_frontend_backend_version_skew_acceptance.py", SKEW),
        ("tests_browser/frontend-backend-version-skew.spec.mjs", SKEW),
        ("tests_real_ha/test_ha_version_upgrade_acceptance.py", UPGRADE),
        ("tests_real_ha/test_hacs_install_update_acceptance.py", HACS),
        ("tests_real_ha/test_ipv6_only_networking_acceptance.py", IPV6),
        ("tests_real_ha/test_browser_compatibility_acceptance.py", BROWSER),
    ],
)
def test_specialist_own_tests_trigger_their_real_environment(path, lane):
    assert select([path], "develop")[lane]


@pytest.mark.parametrize(
    "path", ["README.md", "docs/configuration.md", "tests/test_example.py"]
)
def test_unrelated_changes_do_not_run_specialists(path):
    plan = select([path], "develop")
    assert not any(plan[lane] for lane in SPECIALISTS)


def test_branch_filters_and_mixed_windows_paths_are_preserved():
    path = COMPONENT + "agent_config.py"
    plan = select([path], "maintenance/example")
    assert not plan[SKEW] and not plan[IPV6]
    assert plan[UPGRADE] and plan[HACS] and plan[BROWSER]
    assert select(["docs/configuration.md", path.replace("/", "\\")], "main")[IPV6]


def test_shared_group_changes_force_full_selection():
    assert all(select(["ci/pr_dependency_groups.json"], "develop").values())


def test_selector_cli_writes_dependency_aware_plan(tmp_path, monkeypatch, capsys):
    paths, output = tmp_path / "paths.txt", tmp_path / "github-output.txt"
    paths.write_text(
        COMPONENT + "functions/web.py\ndocs/configuration.md\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_validation.py",
            "select",
            str(paths),
            "--base",
            "develop",
            "--output",
            str(output),
        ],
    )
    assert main() == 0
    plan = json.loads(output.read_text(encoding="utf-8").removeprefix("plan="))
    assert all(plan[lane] for lane in SPECIALISTS)
    assert not plan["ci-image-dev"] and not plan["android-companion-app"]
    assert json.loads(capsys.readouterr().out) == plan


def test_dependency_groups_compose_with_paths_and_ignore_rules():
    policy = {
        "lane": {
            "paths": ["explicit.py"],
            "dependency-groups": ["network"],
            "paths-ignore": ["docs/**"],
        }
    }
    groups = {"network": ["provider/**/*.py", "docs/**"]}
    assert select(["provider/nested/client.py"], "develop", policy, groups) == {
        "lane": True
    }
    assert select(["explicit.py"], "develop", policy, groups) == {"lane": True}
    assert select(["docs/example.md"], "develop", policy, groups) == {"lane": False}
    assert select(["other.py"], "develop", policy, groups) == {"lane": False}
    assert select(
        ["docs/example.md", "provider/client.py"], "develop", policy, groups
    ) == {"lane": True}


def test_group_only_rules_remain_selective_and_respect_branch_exclusions():
    policy = {
        "lane": {"dependency-groups": ["runtime"], "branches-ignore": ["excluded/**"]}
    }
    groups = {"runtime": ["runtime/**/*.py"]}
    assert select(["other.py"], "develop", policy, groups) == {"lane": False}
    assert select(["runtime/worker.py"], "develop", policy, groups) == {"lane": True}
    assert select(["runtime/worker.py"], "excluded/example", policy, groups) == {
        "lane": False
    }


def test_release_upgrade_reuses_runtime_and_frontend_dependencies():
    for path in (COMPONENT + "memory.py", COMPONENT + "frontend/management-panel.js"):
        assert select([path], "develop")["upgrade-acceptance"]


def test_bad_group_is_not_hidden_by_full_selection():
    with pytest.raises(ValueError, match="dependency group"):
        select(
            ["ci/pr_workflows.json"],
            "develop",
            {"lane": {"dependency-groups": ["typo"]}},
            {},
        )


@pytest.mark.parametrize(
    "groups", [{}, {"network": []}, {"network": "provider/**"}, {"network": [None]}]
)
def test_invalid_or_unknown_dependency_groups_fail_closed(groups):
    with pytest.raises(ValueError, match="dependency group"):
        select(
            ["provider/client.py"],
            "develop",
            {"lane": {"dependency-groups": ["network"]}},
            groups,
        )
