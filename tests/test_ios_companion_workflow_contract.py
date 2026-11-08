"""Contract checks for iOS Companion-app acceptance."""

from pathlib import Path
import subprocess
import sys

import yaml

from ci.companion_app_server import write_configuration
from ci.patch_ios_companion_e2e import CALLS, MARKER

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/ios-companion-app.yml").read_text(encoding="utf-8")
    )


def test_ios_companion_workflow_pins_upstream_source_and_exact_candidate():
    data = _workflow()
    job = data["jobs"]["companion"]
    assert job["runs-on"] == "xcode-27"
    assert job["env"]["IOS_SOURCE_SHA"] == ("0a3002690caf19ab6267d7573390798199172146")
    checkout = next(
        step
        for step in job["steps"]
        if step.get("name") == "Checkout exact EOAI candidate"
    )
    assert checkout["with"]["ref"] == (
        "${{ github.event.pull_request.head.sha || github.sha }}"
    )
    clone = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Clone pinned Home Assistant iOS source"
    )
    assert 'checkout --detach "$IOS_SOURCE_SHA"' in clone
    assert "rev-parse HEAD" in clone


def test_ios_companion_workflow_warms_default_branch_without_pr_builds():
    data = _workflow()
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"workflow_dispatch", "push", "schedule"}
    assert triggers["push"] == {
        "branches": ["develop"],
        "paths": [
            ".github/workflows/ios-companion-app.yml",
            "ci/patch_ios_companion_e2e.py",
            "ci/prepare_ios_build_tools.sh",
            "ci/ios_companion_build.py",
            "ci/ios_companion_runtime.py",
        ],
    }
    assert triggers["schedule"] == [
        {"cron": "35 5 * * 1,4"},
        {"cron": "35 5 * * 0,2,3,5,6"},
    ]
    assert "if" not in data["jobs"]["companion"]
    assert data["concurrency"]["queue"] == "max"
    assert "github.ref" not in data["concurrency"]["group"]


def test_ios_companion_uses_official_xcuitest_harness_and_fails_closed():
    job = _workflow()["jobs"]["companion"]
    journey = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run genuine iOS Companion journey"
    )
    assert "bundle exec fastlane e2e" in journey["run"]
    assert journey["if"] == "env.IOS_WARM_ONLY != 'true'"
    assert "continue-on-error" not in journey

    evidence = next(
        step
        for step in job["steps"]
        if step.get("with", {}).get("name") == "ios-companion-app-evidence"
    )
    assert evidence["with"]["if-no-files-found"] == "error"

    record = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Record source-bound iOS Companion evidence"
    )
    assert '"candidate_sha": os.environ["CANDIDATE_SHA"]' in record
    assert '"ios_source_sha": os.environ["IOS_SOURCE_SHA"]' in record
    assert '"management_edit_save": True' in record
    assert '"background_resume": True' in record
    assert 'item["title"] == "Companion app iOS saved title"' in record


def test_ios_e2e_patcher_extends_the_upstream_onboarding_test():
    patcher = (ROOT / "ci/patch_ios_companion_e2e.py").read_text(encoding="utf-8")
    assert "OnboardingE2ETests.swift" in patcher
    assert "openExtendedOpenAIAndEditAgent()" in patcher
    assert "verifyExtendedOpenAIAfterBackgroundResume()" in patcher
    assert "Companion app iOS saved title" in patcher
    assert "XCUIDevice.shared.press(.home)" in patcher
    assert '"keyboard Done button"' in patcher
    assert "app.keyboards.firstMatch.waitForNonExistence" in patcher
    assert patcher.index("keyboardDone.tap()") < patcher.index(
        'tapWebElement(labelContaining: "save changes"'
    )
    assert "-collect-test-diagnostics never" in patcher
    assert "until: settings" in patcher
    assert "sidebarScrolls < 6" in patcher
    assert "let pageSelector = webView.descendants(matching: .any)" in patcher
    assert (
        'NSPredicate(format: "label == %@ AND value == %@", "Page", "Overview")'
        in patcher
    )
    assert (
        'NSPredicate(format: "label == %@ AND value == %@", "Page", "Assistant")'
        in patcher
    )
    assert 'NSPredicate(format: "label == %@", "Assistant")' in patcher
    assert 'tapWebElement(labelContaining: "assistant"' not in patcher


def test_ios_companion_caches_expensive_build_state_across_retries():
    job = _workflow()["jobs"]["companion"]
    steps = job["steps"]

    restore_derived = next(
        step for step in steps if step.get("name") == "Restore iOS DerivedData cache"
    )
    assert restore_derived["uses"].startswith("actions/cache/restore@")
    assert "IOS_INCREMENTAL_CACHE_ID" in restore_derived["with"]["key"]
    assert "IOS_CACHE_COMPATIBILITY" in restore_derived["with"]["restore-keys"]
    assert "github.sha" not in restore_derived["with"]["key"]
    assert "Logs" not in restore_derived["with"]["path"]
    assert restore_derived["with"]["path"].endswith("/ModuleCache.noindex\n")
    identity = next(
        step["run"]
        for step in steps
        if step.get("name") == "Resolve exact native build cache identity"
    )
    assert 'ios_companion_build.py identity "$IOS_DIR"' in identity

    save_derived = next(
        step for step in steps if step.get("name") == "Save iOS DerivedData cache"
    )
    assert save_derived["uses"].startswith("actions/cache/save@")
    assert "github.event.repository.default_branch" in save_derived["if"]
    assert "steps.ios-native-build.outcome == 'success'" in save_derived["if"]

    ruby = next(
        step for step in steps if step.get("uses", "").startswith("ruby/setup-ruby@")
    )
    assert ruby["with"]["bundler-cache"] is True
    assert ruby["with"]["working-directory"] == "${{ env.IOS_DIR }}"
    install = next(step for step in steps if step.get("id") == "ios-packages-resolve")
    assert "xcodebuild -resolvePackageDependencies" in install["run"]
    assert "-clonedSourcePackagesDirPath" in install["run"]


def test_ios_host_tools_are_prepared_for_cold_builds_before_the_journey():
    steps = _workflow()["jobs"]["companion"]["steps"]
    preparation = next(
        step for step in steps if step.get("id") == "ios-buildtools-prepare"
    )
    journey = next(
        step
        for step in steps
        if step.get("name") == "Run genuine iOS Companion journey"
    )
    assert "prepare_ios_build_tools.sh" in preparation["run"]
    assert preparation["timeout-minutes"] == 20
    assert preparation["if"] == "steps.ios-native.outputs.hit != 'true'"
    assert steps.index(preparation) < steps.index(journey)
    assert "continue-on-error" not in journey
    restored = next(step for step in steps if step.get("id") == "ios-buildtools-cache")
    saved = next(
        step for step in steps if step.get("name") == "Save iOS host build-tool cache"
    )
    assert restored["with"]["path"] == saved["with"]["path"]
    assert restored["with"]["key"] == saved["with"]["key"]
    assert "IOS_TOOLS_CACHE_ID" in restored["with"]["key"]
    assert "BuildTools/.build" in restored["with"]["path"]
    assert "${{ env.IOS_DIR }}/.build" in restored["with"]["path"]
    assert "steps.ios-buildtools-prepare.outcome == 'success'" in saved["if"]
    record = next(
        step
        for step in steps
        if step.get("name") == "Record source-bound iOS Companion evidence"
    )
    assert record["if"] == "success() && env.IOS_WARM_ONLY != 'true'"


def test_compiled_artifact_reuse_does_not_reuse_acceptance_evidence():
    workflow = _workflow()
    assert workflow["permissions"]["actions"] == "write"
    steps = workflow["jobs"]["companion"]["steps"]
    build = next(step for step in steps if step.get("id") == "ios-native-build")
    assert build["if"] == "steps.ios-native.outputs.hit != 'true'"
    assert build["run"] == "bundle exec fastlane eoai_build"
    publish = next(
        step
        for step in steps
        if step.get("name") == "Publish reusable compiled iOS test artifact"
    )
    assert publish["with"]["path"].endswith("/native-products.tar")
    journey = next(
        step
        for step in steps
        if step.get("name") == "Run genuine iOS Companion journey"
    )
    assert steps.index(publish) < steps.index(journey)
    assert journey["if"] == "env.IOS_WARM_ONLY != 'true'"
    record = next(
        step
        for step in steps
        if step.get("name") == "Record source-bound iOS Companion evidence"
    )
    assert record["if"] == "success() && env.IOS_WARM_ONLY != 'true'"
    assert '"native_build_identity"' in record["run"]


def test_ios_fixture_uses_a_dashboard_with_an_unobstructed_header(tmp_path):
    job = _workflow()["jobs"]["companion"]
    startup = next(
        step
        for step in job["steps"]
        if step.get("name") == "Start disposable Home Assistant"
    )
    assert "--classic-dashboard" in startup["run"]
    write_configuration(tmp_path, classic_dashboard=True)
    config = yaml.safe_load((tmp_path / "configuration.yaml").read_text())
    assert config["lovelace"]["mode"] == "yaml"
    dashboard = yaml.safe_load((tmp_path / "ui-lovelace.yaml").read_text())
    assert dashboard["views"][0]["cards"][0]["type"] == "markdown"


def test_default_companion_fixture_keeps_its_existing_dashboard(tmp_path):
    write_configuration(tmp_path)
    assert "lovelace" not in yaml.safe_load(
        (tmp_path / "configuration.yaml").read_text()
    )
    assert not (tmp_path / "ui-lovelace.yaml").exists()


def test_diagnostics_patch_targets_e2e_instead_of_the_first_unit_lane(tmp_path):
    target = tmp_path / "Tests/UI/OnboardingE2ETests.swift"
    target.parent.mkdir(parents=True)
    target.write_text(CALLS + MARKER, encoding="utf-8")
    lane = tmp_path / "fastlane/lanes/testing.rb"
    lane.parent.mkdir(parents=True)
    options = "    result_bundle: true,\n    skip_package_dependencies_resolution: true,\n    xcargs: 'COMPILER_INDEX_STORE_ENABLE=NO',\n"
    unit_lane = "lane :test do\n" + options + "end\n"
    lane.write_text(
        unit_lane
        + 'lane :e2e do |options|\n  udid = newest_simulator(model, \'iOS\')\n  sh("xcrun simctl shutdown #{udid} || true")\n  sh("xcrun simctl erase #{udid}")\n'
        + options
        + "end\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(ROOT / "ci/patch_ios_companion_e2e.py"), str(tmp_path)],
        check=True,
    )
    patched = lane.read_text(encoding="utf-8")
    earlier, _, e2e = patched.partition("lane :e2e do |options|")
    assert earlier == unit_lane
    assert "-collect-test-diagnostics never" in e2e
    assert "collect_test_diagnostics:" not in patched
    assert 'sh("xcrun simctl boot #{udid}")' not in e2e
    assert 'sh("xcrun simctl erase #{udid}")' not in e2e
    assert "ENV.fetch('IOS_SIMULATOR_UDID')" in e2e
    assert "test_without_building: true" in e2e
    assert "build_for_testing: true" in e2e
    assert "lane :eoai_build do" in e2e
    assert "derived_data_path:" in e2e
    assert "cloned_source_packages_path:" in e2e


def test_services_start_before_compilation_but_readiness_gates_testing():
    steps = _workflow()["jobs"]["companion"]["steps"]
    names = [step.get("name") for step in steps]
    build = names.index("Build pinned iOS app and test runner once")
    ready = names.index("Wait for fresh simulator and Home Assistant")
    assert names.index("Start fresh iOS simulator in background") < build
    assert names.index("Start disposable Home Assistant") < build
    assert build < ready < names.index("Run genuine iOS Companion journey")
    ha = steps[names.index("Start disposable Home Assistant")]["run"]
    assert "sleep" not in ha
    assert "--classic-dashboard" in ha
