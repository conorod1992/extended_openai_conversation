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
        (ROOT / ".github/workflows/ios-companion-app.yml").read_text(
            encoding="utf-8"
        )
    )


def test_ios_companion_workflow_pins_upstream_source_and_exact_candidate():
    data = _workflow()
    job = data["jobs"]["companion"]
    assert job["runs-on"] == "xcode-27"
    assert job["env"]["IOS_SOURCE_SHA"] == (
        "0a3002690caf19ab6267d7573390798199172146"
    )
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


def test_ios_companion_workflow_runs_on_introducing_pr_and_not_every_pr():
    job = _workflow()["jobs"]["companion"]
    condition = job["if"]
    assert "github.event_name != 'pull_request'" in condition
    assert "github.event.pull_request.number == 1136" in condition


def test_ios_companion_uses_official_xcuitest_harness_and_fails_closed():
    job = _workflow()["jobs"]["companion"]
    journey = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run genuine iOS Companion journey"
    )
    assert "bundle exec fastlane e2e" in journey["run"]

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
    assert 'app.keyboards.firstMatch.waitForNonExistence' in patcher
    assert patcher.index('keyboardDone.tap()') < patcher.index('tapWebElement(labelContaining: "save changes"')
    assert "-collect-test-diagnostics never" in patcher
    assert 'until: settings' in patcher
    assert "sidebarScrolls < 6" in patcher
    assert 'NSPredicate(format: "label == %@", "Page")' in patcher
    assert 'NSPredicate(format: "label == %@", "Assistant")' in patcher
    assert 'tapWebElement(labelContaining: "assistant"' not in patcher


def test_ios_companion_caches_expensive_build_state_across_retries():
    job = _workflow()["jobs"]["companion"]
    steps = job["steps"]

    restore_derived = next(
        step for step in steps if step.get("name") == "Restore iOS DerivedData cache"
    )
    assert restore_derived["uses"].startswith("actions/cache/restore@")
    assert "IOS_SOURCE_SHA" in restore_derived["with"]["key"]
    assert "github.sha" not in restore_derived["with"]["key"]

    restore_gems = next(
        step for step in steps if step.get("name") == "Restore Bundler cache"
    )
    assert restore_gems["uses"].startswith("actions/cache/restore@")
    assert "IOS_SOURCE_SHA" in restore_gems["with"]["key"]

    save_derived = next(
        step for step in steps if step.get("name") == "Save iOS DerivedData cache"
    )
    assert save_derived["uses"].startswith("actions/cache/save@")
    assert "always()" in save_derived["if"]

    save_gems = next(
        step for step in steps if step.get("name") == "Save Bundler cache"
    )
    assert save_gems["uses"].startswith("actions/cache/save@")
    assert "always()" in save_gems["if"]

    install = next(
        step for step in steps
        if step.get("name") == "Install pinned iOS build dependencies"
    )
    assert "steps.ios-gems-cache.outputs.cache-hit" in install["run"]
    assert "xcodebuild -resolvePackageDependencies" in install["run"]


def test_ios_fixture_uses_a_dashboard_with_an_unobstructed_header(tmp_path):
    job = _workflow()["jobs"]["companion"]
    startup = next(step for step in job["steps"] if step.get("name") == "Start disposable Home Assistant")
    assert "--classic-dashboard" in startup["run"]
    write_configuration(tmp_path, classic_dashboard=True)
    config = yaml.safe_load((tmp_path / "configuration.yaml").read_text())
    assert config["lovelace"]["mode"] == "yaml"
    dashboard = yaml.safe_load((tmp_path / "ui-lovelace.yaml").read_text())
    assert dashboard["views"][0]["cards"][0]["type"] == "markdown"


def test_default_companion_fixture_keeps_its_existing_dashboard(tmp_path):
    write_configuration(tmp_path)
    assert "lovelace" not in yaml.safe_load((tmp_path / "configuration.yaml").read_text())
    assert not (tmp_path / "ui-lovelace.yaml").exists()


def test_diagnostics_patch_targets_e2e_instead_of_the_first_unit_lane(tmp_path):
    target = tmp_path / "Tests/UI/OnboardingE2ETests.swift"
    target.parent.mkdir(parents=True)
    target.write_text(CALLS + MARKER, encoding="utf-8")
    lane = tmp_path / "fastlane/lanes/testing.rb"
    lane.parent.mkdir(parents=True)
    options = "    result_bundle: true,\n    skip_package_dependencies_resolution: true,\n    xcargs: 'COMPILER_INDEX_STORE_ENABLE=NO',\n"
    unit_lane = "lane :test do\n" + options + "end\n"
    lane.write_text(unit_lane + "lane :e2e do |options|\n" + options + "end\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "ci/patch_ios_companion_e2e.py"), str(tmp_path)], check=True)
    patched = lane.read_text(encoding="utf-8")
    earlier, _, e2e = patched.partition("lane :e2e do |options|")
    assert earlier == unit_lane
    assert "-collect-test-diagnostics never" in e2e
    assert 'collect_test_diagnostics:' not in patched
