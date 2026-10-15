"""Comparability depends on resolved pins/runtime, not declaration bytes."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yaml

from ci.enhanced_evidence import environment_fingerprint
from ci.frontend_latency import environment
from ci.frontend_latency.compare import main as compare
from ci.frontend_latency.review import check_identity


def test_different_ranges_accept_same_installed_version():
    packages = {"openai": "3.10.0", "pytest": "9.0.0"}
    for declared in (
        [("baseline-manifest.json", "openai>=2.21.0,<=3.10.0")],
        [("current-manifest.json", "openai>=2.21.0,<=3.26.0")],
        [("requirements_test.txt", "pytest>=8")],
    ):
        assert environment.compatibility_errors(declared, packages) == []


def test_incompatible_and_missing_dependencies_name_source_and_pin():
    errors = environment.compatibility_errors(
        [
            ("baseline-manifest.json", "openai<=3.10.0"),
            ("requirements_test.txt", "pytest>=8"),
        ],
        {"openai": "3.26.0"},
    )
    assert errors == [
        "baseline-manifest.json: requires openai<=3.10.0; resolved openai==3.26.0",
        "requirements_test.txt: requires pytest>=8; resolved pytest==<missing>",
    ]


def test_names_markers_and_prereleases_follow_pep508():
    assert (
        environment.compatibility_errors(
            [
                ("requirements", "Some_Package>=2.0b1"),
                ("requirements", 'absent; python_version < "3"'),
            ],
            {"some-package": "2.0b2"},
        )
        == []
    )


def test_requested_extras_must_be_installed(monkeypatch):
    monkeypatch.setattr(
        environment,
        "requires",
        lambda _: ['transport>=2; extra == "http"', 'other>=1; extra == "other"'],
    )
    errors = environment.compatibility_errors(
        [("manifest", "sdk[http]>=1")], {"sdk": "1"}
    )
    assert errors == [
        "manifest (sdk[http]>=1 extra): requires transport>=2; resolved transport==<missing>"
    ]
    assert (
        environment.compatibility_errors(
            [("manifest", "sdk[http]>=1")], {"sdk": "1", "transport": "2"}
        )
        == []
    )


@pytest.mark.parametrize(
    "requirement", ["-r other.txt", "sdk @ https://example.com/sdk.whl"]
)
def test_unverifiable_declarations_fail_closed(requirement):
    with pytest.raises(ValueError, match=r"baseline\.txt"):
        environment.compatibility_errors([("baseline.txt", requirement)], {})


def test_collects_recursive_ha_dependencies_ignoring_manifest_metadata(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# comment\npytest>=8  # test runner\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "changed",
                "requirements": ["openai>=2"],
                "dependencies": ["conversation"],
            }
        ),
        encoding="utf-8",
    )
    components = tmp_path / "components"
    for domain, data in {
        "conversation": {
            "requirements": ["hassil==3"],
            "after_dependencies": ["other"],
        },
        "other": {"requirements": ["helper==1"], "dependencies": ["conversation"]},
    }.items():
        path = components / domain / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    declared, overridden = environment.declarations(requirements, manifest, components)
    assert overridden == []
    assert {value for _, value in declared} == {
        "pytest>=8",
        "openai>=2",
        "hassil==3",
        "helper==1",
    }


def test_runtime_integration_pin_overrides_test_bootstrap_without_package_exemption(
    tmp_path,
):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("Gazetteer_Matcher==1.0.0\npytest>=8\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"dependencies": ["conversation"]}), encoding="utf-8"
    )
    components = tmp_path / "components"
    conversation = components / "conversation" / "manifest.json"
    conversation.parent.mkdir(parents=True)
    conversation.write_text(
        json.dumps({"requirements": ["gazetteer-matcher==1.1.0"]}), encoding="utf-8"
    )
    declared, overridden = environment.declarations(requirements, manifest, components)
    assert (
        environment.compatibility_errors(
            declared, {"gazetteer-matcher": "1.1.0", "pytest": "9"}
        )
        == []
    )
    assert overridden == [
        {
            "source": str(requirements),
            "requirement": "Gazetteer_Matcher==1.0.0",
            "runtime_declarations": [(str(conversation), "gazetteer-matcher==1.1.0")],
        }
    ]
    assert (str(requirements), "pytest>=8") in declared


def test_baseline_current_runtime_constraints_are_never_overridden(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sdk==1\n", encoding="utf-8")
    declared = []
    for label, pin in (("baseline", "sdk==2"), ("current", "sdk==3")):
        manifest = tmp_path / f"{label}.json"
        manifest.write_text(json.dumps({"requirements": [pin]}), encoding="utf-8")
        effective, overridden = environment.declarations(
            requirements, manifest, tmp_path
        )
        assert len(overridden) == 1
        declared.extend(effective)
    errors = environment.compatibility_errors(declared, {"sdk": "3"})
    assert len(errors) == 1
    assert "baseline.json: requires sdk==2; resolved sdk==3" in errors[0]


def test_inactive_runtime_requirement_does_not_override_test_bootstrap(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sdk==1\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"requirements": ['sdk==2; python_version < "3"']}), encoding="utf-8"
    )
    effective, overridden = environment.declarations(requirements, manifest, tmp_path)
    assert overridden == []
    assert environment.compatibility_errors(effective, {"sdk": "2"})


def test_overridden_bootstrap_preserves_requested_extras(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("sdk[http]==1\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"requirements": ["sdk==2"]}), encoding="utf-8")
    effective, overridden = environment.declarations(requirements, manifest, tmp_path)
    assert overridden[0]["requirement"] == "sdk[http]==1"
    assert (str(requirements), "sdk[http]") in effective
    monkeypatch.setattr(
        environment, "requires", lambda _: ['transport>=2; extra == "http"']
    )
    assert environment.compatibility_errors(effective, {"sdk": "2"})
    assert (
        environment.compatibility_errors(effective, {"sdk": "2", "transport": "2"})
        == []
    )


@pytest.mark.parametrize(
    "component,old,new",
    [
        ("python", "3.14.2", "3.14.3"),
        ("architecture", "x86_64", "aarch64"),
        ("node", "v24.19.0", "v24.20.0"),
        ("homeassistant_source_commit", "a" * 40, "b" * 40),
    ],
)
def test_runtime_diagnostics_identify_component(component, old, new):
    assert environment.environment_differences({component: old}, {component: new}) == [
        f"environment.{component}: baseline={old!r}, candidate={new!r}"
    ]


def test_transitive_packages_and_browser_diagnostics():
    baseline = {
        "python_packages": {"openai": "3.10.0", "anyio": "4.0"},
        "browser": {"chromium": "148"},
    }
    current = deepcopy(baseline)
    current["python_packages"]["anyio"] = "4.1"
    current["python_packages"]["added"] = "1"
    current["browser"]["chromium"] = "149"
    errors = environment.environment_differences(baseline, current)
    assert errors == [
        "environment.browser.chromium: baseline='148', candidate='149'",
        "environment.python_packages.added: baseline='<missing>', candidate='1'",
        "environment.python_packages.anyio: baseline='4.0', candidate='4.1'",
    ]


def test_prepare_joint_resolution_keeps_image_ha_pins(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest>=8\n", encoding="utf-8")
    manifests = []
    for label, ceiling in (("baseline", "3.10.0"), ("current", "3.26.0")):
        manifest = tmp_path / f"{label}.json"
        manifest.write_text(
            json.dumps({"requirements": [f"openai>=2.21.0,<={ceiling}"]}),
            encoding="utf-8",
        )
        manifests.append(manifest)
    output = tmp_path / "environment.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "environment",
            "--baseline-requirements",
            str(requirements),
            "--current-requirements",
            str(requirements),
            "--baseline-manifest",
            str(manifests[0]),
            "--current-manifest",
            str(manifests[1]),
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(
        environment,
        "version",
        lambda name: {
            "homeassistant": "2026.10.1",
            "pytest-homeassistant-custom-component": "0.13.400",
        }[name],
    )
    incompatible = {
        "openai": "3.26.0",
        "pytest": "9",
        "homeassistant": "2026.10.1",
        "pytest-homeassistant-custom-component": "0.13.400",
    }
    compatible = {**incompatible, "openai": "3.10.0"}
    # Isolate the installed HA package_constraints from this machine's version.
    monkeypatch.setattr(
        environment,
        "_requirements",
        lambda path: ["pytest>=8"] if path == requirements else ["openai>=2.21.0"],
    )
    monkeypatch.setattr(
        environment, "installed_packages", Mock(side_effect=[incompatible, compatible])
    )
    monkeypatch.setattr(
        environment, "environment_identity", lambda: {"python_packages": compatible}
    )
    run = Mock()
    monkeypatch.setattr(environment.subprocess, "run", run)
    environment.main()
    install = run.call_args_list[0].args[0]
    assert install[:4] == [sys.executable, "-m", "pip", "install"]
    assert "homeassistant==2026.10.1" in install
    assert "pytest-homeassistant-custom-component==0.13.400" in install
    assert "openai<=3.10.0,>=2.21.0" in install
    assert "openai<=3.26.0,>=2.21.0" in install
    assert (
        json.loads(output.read_text(encoding="utf-8"))["environment"]["python_packages"]
        == compatible
    )
    run.reset_mock()
    monkeypatch.setattr(environment, "installed_packages", lambda: compatible)
    environment.main()
    assert run.call_args.args[0] == [sys.executable, "-m", "pip", "check"]
    assert run.call_count == 1  # A compatible image needs no reinstall.
    monkeypatch.setattr(environment, "installed_packages", lambda: incompatible)
    run.side_effect = subprocess.CalledProcessError(1, "pip")
    with pytest.raises(
        SystemExit,
        match=r"Cannot resolve a shared environment.*baseline was not changed",
    ):
        environment.main()


def test_standalone_comparison_rejects_drift_with_specific_diagnostic(
    tmp_path, monkeypatch
):
    baseline = {"python_packages": {"openai": "3.10.0"}, "node": "v24.20.0"}
    current = {**baseline, "python_packages": {"openai": "3.26.0"}}
    paths = []
    for label, identity in (("baseline", baseline), ("current", current)):
        path = tmp_path / f"{label}.json"
        path.write_text(
            json.dumps(
                {
                    "environment": identity,
                    "environment_sha256": environment_fingerprint(identity),
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)
    monkeypatch.setattr(
        sys, "argv", ["compare", *map(str, paths), str(tmp_path / "output")]
    )
    with pytest.raises(
        SystemExit,
        match=r"environment\.python_packages\.openai: baseline='3\.10\.0', candidate='3\.26\.0'",
    ):
        compare()
    assert not (tmp_path / "output").exists()


def test_fingerprint_alone_does_not_replace_resolved_environment_evidence():
    identity = {"python": "3.14.2"}
    assert (
        "Missing resolved Python package pins or Node runtime identity"
        in check_identity(
            {
                "eoai_sha": "a" * 40,
                "environment": identity,
                "environment_sha256": environment_fingerprint(identity),
            },
            "a" * 40,
        )
    )


def test_prepared_pins_reject_drift_before_or_during_benchmark(tmp_path, monkeypatch):
    prepared = {"python_packages": {"anyio": "4.0"}, "node": "v24.20.0"}
    path = tmp_path / "environment.json"
    path.write_text(json.dumps({"environment": prepared}), encoding="utf-8")
    monkeypatch.setenv("EOAI_LATENCY_ENVIRONMENT", str(path))
    monkeypatch.setattr(environment, "environment_identity", lambda: deepcopy(prepared))
    assert environment.verified_environment_identity() == prepared
    changed = {**prepared, "python_packages": {"anyio": "4.1"}}
    monkeypatch.setattr(environment, "environment_identity", lambda: changed)
    with pytest.raises(
        SystemExit,
        match=r"environment\.python_packages\.anyio: baseline='4\.0', candidate='4\.1'",
    ):
        environment.verified_environment_identity()


def test_workflow_prepares_both_refs_and_reuses_current_guarded_harness():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/frontend-latency-diagnostics.yml").read_text(
            encoding="utf-8"
        )
    )
    job = workflow["jobs"]["diagnose"]
    steps = {step["name"]: step for step in job["steps"]}
    prepare = next(
        step for step in job["steps"] if step.get("id") == "dependency_check"
    )
    assert "python -m ci.frontend_latency.environment" in prepare["run"]
    for label in ("baseline", "current"):
        assert (
            f"--{label}-requirements /tmp/{label}-requirements_test.txt"
            in prepare["run"]
        )
        assert f"--{label}-manifest /tmp/{label}-manifest.json" in prepare["run"]
    assert "--output latency-results/environment.json" in prepare["run"]
    assert (
        job["env"]["EOAI_LATENCY_ENVIRONMENT"]
        == "${{ github.workspace }}/latency-results/environment.json"
    )
    assert (
        'git checkout "$CURRENT_SHA" -- ci/frontend_latency'
        in steps["Measure baseline"]["run"]
    )
    assert (
        "steps.dependency_check.outcome == 'success'"
        in steps["Require complete exact-source frontend evidence"]["if"]
    )
    for source in ("test_latency_diagnostics.py", "test_frontend_accessibility.py"):
        text = (root / "ci/frontend_latency" / source).read_text(encoding="utf-8")
        before, after = text.split("await _run_playwright(", 1)
        assert "verified_environment_identity()" in before
        assert "verified_environment_identity()" in after
