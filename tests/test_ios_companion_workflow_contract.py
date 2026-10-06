"""Contract checks for iOS Companion-app acceptance."""

from pathlib import Path

import yaml

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
    assert "test/ios-companion-app-acceptance" in condition


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
