"""Contract checks for Android Companion-app acceptance."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/android-companion-app.yml").read_text(
            encoding="utf-8"
        )
    )


def test_companion_workflow_pins_released_app_and_exact_candidate():
    data = _workflow()
    job = data["jobs"]["companion"]
    assert job["env"]["COMPANION_VERSION"] == "2026.8.4"
    assert len(job["env"]["COMPANION_APK_SHA256"]) == 64
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    emulator = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run real Companion app journey"
    )
    assert (
        emulator["uses"]
        == "reactivecircus/android-emulator-runner@a421e43855164a8197daf9d8d40fe71c6996bb0d"
    )
    assert "adb install -r" in emulator["with"]["script"]
    assert "bash ci/run_android_companion.sh" in emulator["with"]["script"]
    runner = (ROOT / "ci/run_android_companion.sh").read_text(encoding="utf-8")
    assert "maestro test tests_mobile/companion-app-smoke.yaml" in runner
    assert runner.index("adb shell pm clear") < runner.index("maestro test")
    assert "adb wait-for-device" in runner
    assert '[[ "$stable" == 5 ]]' in runner


def test_companion_evidence_fails_closed_and_names_native_boundaries():
    job = _workflow()["jobs"]["companion"]
    evidence = next(
        step
        for step in job["steps"]
        if step.get("with", {}).get("name") == "android-companion-app-evidence"
    )
    assert evidence["with"]["if-no-files-found"] == "error"
    record = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Record source-bound Companion evidence"
    )
    assert '"candidate_sha": os.environ["CANDIDATE_SHA"]' in record
    assert '"management_edit_save": True' in record
    assert '"background_resume": True' in record
    assert '"deep_link_reentry": True' in record
    assert 'entry["subentries"]' in record
    assert 'item["title"] == "Companion app saved title"' in record


def test_companion_flow_uses_real_release_app_and_native_navigation_boundary():
    flow = (ROOT / "tests_mobile/companion-app-smoke.yaml").read_text(
        encoding="utf-8"
    )
    assert "appId: io.homeassistant.companion.android" in flow
    assert "Enter address manually" in flow
    assert "Forgot password?" in flow
    assert "homeassistant://navigate/extended-openai/assistant/basics" in flow
    assert "Agent name" in flow
    assert 'inputText: "Companion app saved title"' in flow
    assert "pressKey: Home" in flow
    assert "clearState: false" in flow
