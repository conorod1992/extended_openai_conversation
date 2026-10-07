"""Contract checks for official Home Assistant Container architecture coverage."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/official-ha-container.yml").read_text(
            encoding="utf-8"
        )
    )


def test_official_container_matrix_covers_native_amd64_and_arm64():
    job = _workflow()["jobs"]["official-container"]
    include = job["strategy"]["matrix"]["include"]
    assert {
        (
            item["arch"],
            item["runner"],
            item["machine"],
            item["image_arch"],
        )
        for item in include
    } == {
        ("amd64", "ubuntu-latest", "x86_64", "amd64"),
        ("arm64", "ubuntu-24.04-arm", "aarch64", "arm64"),
    }


def test_official_container_workflow_fails_closed_on_architecture_mismatch():
    job = _workflow()["jobs"]["official-container"]
    prove = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Prove runner architecture"
    )
    resolve = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Resolve stable Home Assistant image"
    )
    exercise = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Exercise staged EOAI inside official runtime"
    )
    assert 'test "$ACTUAL" = "$EXPECTED_MACHINE"' in prove
    assert "docker image inspect" in resolve
    assert 'test "$IMAGE_ARCH" = "$EXPECTED_IMAGE_ARCH"' in resolve
    assert 'test "$CONTAINER_MACHINE" = "${{ matrix.machine }}"' in resolve
    assert "--expected-machine" in exercise
    assert "--expected-image-arch" in exercise


def test_official_container_evidence_is_architecture_bound():
    source = (ROOT / "ci/official_container_acceptance.py").read_text(
        encoding="utf-8"
    )
    assert '"container_machine": container_machine.stdout.strip()' in source
    assert '"image_architecture": image_arch.stdout.strip()' in source
    assert "platform.machine() == args.expected_machine" in source



def test_official_container_cadence_is_daily_amd64_weekly_arm64_and_manual_both():
    data = _workflow()
    triggers = data.get("on", data.get(True))
    assert {item["cron"] for item in triggers["schedule"]} == {
        "7 6 * * *",
        "17 6 * * 0",
    }
    condition = data["jobs"]["official-container"]["if"]
    assert "matrix.arch == 'amd64'" in condition
    assert "github.event.schedule == '17 6 * * 0'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event.pull_request.number == 1137" in condition
