"""Keep candidate checkout and release matrix policy aligned with actual workflows."""

import ast
import json
from pathlib import Path
import re

import yaml

from ci.release_certification import (
    ADVISORY_SDK_LANE,
    SUPPORTED_SDK_LANES,
    UPGRADE_EPOCHS,
)

ROOT = Path(__file__).resolve().parents[1]


def workflow(filename):
    return yaml.safe_load(
        (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
    )


def test_every_enhanced_job_checks_out_the_one_prepare_candidate():
    jobs = workflow("enhanced-stress.yml")["jobs"]
    assert (
        jobs["prepare"]["outputs"]["candidate_sha"]
        == "${{ steps.candidate.outputs.sha }}"
    )
    checkouts = []
    for name, job in jobs.items():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                checkouts.append(name)
                assert step["with"]["ref"] == (
                    "${{ github.sha }}"
                    if name == "prepare"
                    else "${{ needs.prepare.outputs.candidate_sha }}"
                )
    assert set(checkouts) == set(jobs)
    step = next(
        step
        for step in jobs["certify"]["steps"]
        if step.get("name") == "Consolidate actual job results"
    )
    assert (
        step["env"]["ENHANCED_CANDIDATE_SHA"]
        == "${{ needs.prepare.outputs.candidate_sha }}"
    )
    assert jobs["prepare"]["outputs"]["ha_version"] == "${{ steps.ha.outputs.version }}"
    assert (
        step["env"]["ENHANCED_EXPECTED_STABLE_HA_VERSION"]
        == "${{ needs.prepare.outputs.ha_version }}"
    )


def test_enhanced_dispatch_can_run_the_scheduled_intensity_matrix():
    data = workflow("enhanced-stress.yml")
    triggers = data.get("on", data.get(True))
    intensity = triggers["workflow_dispatch"]["inputs"]["intensity"]
    assert intensity["options"] == ["normal", "heavy", "all"]

    jobs = data["jobs"]
    controls = next(
        step
        for step in jobs["prepare"]["steps"]
        if step.get("name") == "Resolve reproducible run controls"
    )
    assert 'INTENSITY" == all' in controls["run"]
    assert 'intensities=["normal","heavy"]' in controls["run"]
    scheduled = next(line for line in controls["run"].splitlines() if "'') CAMPAIGNS=" in line)
    assert '"long-lifetime"' in scheduled
    exclusion = jobs["python-campaigns"]["strategy"]["matrix"]["exclude"]
    assert "github.event_name == 'schedule'" in exclusion
    assert '{"campaign":"long-lifetime","intensity":"heavy"}' in exclusion


def test_existing_historical_matrix_supports_complete_dispatch_and_exact_checkout():
    upgrade = workflow("upgrade-acceptance.yml")["jobs"]["upgrade"]
    matrix = upgrade["strategy"]["matrix"]["from_version"]
    epochs = json.loads(re.search(r"'(\[\"latest\"[^']+\])'", matrix).group(1))
    assert epochs == list(UPGRADE_EPOCHS)
    assert "inputs.from_version == 'all'" in matrix
    checkout = next(
        step
        for step in upgrade["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert (
        checkout["with"]["ref"]
        == "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
    )
    assert any(
        step.get("with", {}).get("name")
        == "upgrade-evidence-${{ matrix.from_version }}"
        and step.get("if") == "always()"
        for step in upgrade["steps"]
    )


def test_supported_sdk_policy_matches_matrix_and_manifest_ceiling():
    sdk = workflow("openai-sdk-compatibility.yml")["jobs"]["sdk-contract"]
    assert sdk["strategy"]["matrix"]["sdk"] == [*SUPPORTED_SDK_LANES, ADVISORY_SDK_LANE]
    assert sdk["continue-on-error"] == "${{ matrix.sdk == 'latest-3x-early-warning' }}"
    checkout = next(
        step
        for step in sdk["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    manifest = json.loads(
        (
            ROOT
            / "custom_components/extended_openai_conversation_responses/manifest.json"
        ).read_text(encoding="utf-8")
    )
    supported = next(
        value for value in manifest["requirements"] if value.startswith("openai")
    )
    floor, ceiling = re.fullmatch(r"openai>=([0-9.]+),<=([0-9.]+)", supported).groups()
    assert (SUPPORTED_SDK_LANES[0], SUPPORTED_SDK_LANES[-1]) == (floor, ceiling)


def test_mutation_dispatch_can_certify_the_complete_campaign_matrix():
    data = workflow("mutation.yml")
    triggers = data.get("on", data.get(True))
    options = triggers["workflow_dispatch"]["inputs"]["campaign"]["options"]
    assert options[0] == "all"
    matrix = data["jobs"]["mutation"]["strategy"]["matrix"]["campaign"]
    assert "inputs.campaign == 'all'" in matrix
    for campaign in (
        "function-tools",
        "guest-security",
        "ha-permissions",
        "request-rules",
        "function-groups",
        "contract-sensitivity",
    ):
        assert campaign in matrix


def test_official_container_workflow_binds_image_evidence_to_exact_candidate():
    data = workflow("official-ha-container.yml")
    job = data["jobs"]["official-container"]
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    runner = next(
        step
        for step in job["steps"]
        if step.get("name") == "Exercise staged EOAI inside official runtime"
    )
    assert "--candidate-sha \"$CANDIDATE_SHA\"" in runner["run"]
    assert "ghcr.io/home-assistant/home-assistant:" in next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Resolve stable Home Assistant image"
    )
    upload = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["with"]["name"] == "official-ha-container-evidence"
    assert upload["with"]["if-no-files-found"] == "error"


def test_standalone_certification_imports_support_the_lightweight_runner_python():
    # The final gate uses ubuntu-latest's Python, independently of HA's Python.
    # Parse its complete dependency chain using that runner's older grammar.
    for filename in (
        "enhanced_certification.py",
        "candidate_evidence.py",
        "compatibility_evidence.py",
        "enhanced_evidence.py",
        "execution_contract.py",
        "release_certification.py",
    ):
        path = ROOT / "ci" / filename
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 12),
        )


def test_enhanced_prebuilt_lanes_keep_exact_browser_ha_and_certification_coverage():
    jobs = workflow("enhanced-stress.yml")["jobs"]
    assert jobs["ha-lifecycle-matrix"]["strategy"]["matrix"]["ha-version"] == [
        "oldest",
        "stable",
    ]
    assert jobs["ha-lifecycle-matrix"]["container"]["image"].endswith(":ha-stable")
    lifecycle_step = next(
        step
        for step in jobs["ha-lifecycle-matrix"]["steps"]
        if step.get("name") == "Install test and selected HA environment"
    )
    assert lifecycle_step["shell"] == "bash"
    lifecycle_install = lifecycle_step["run"]
    assert "pytest-homeassistant-custom-component==0.13.317" in lifecycle_install
    assert "homeassistant==$MINIMUM" in lifecycle_install
    assert (
        "apt-get install -y --no-install-recommends build-essential"
        in lifecycle_install
    )
    assert 'test "$(python -c' in lifecycle_install
    assert '= "$MINIMUM"' in lifecycle_install
    assert "$EOAI_EXPECTED_HA_VERSION" in lifecycle_install
    assert jobs["ha-lifecycle-dev"]["steps"]
    assert any(
        step.get("name") == "Resolve exact HA dev commit and Python version"
        for step in jobs["ha-lifecycle-dev"]["steps"]
    )
    assert any(
        step.get("name")
        == "Construct the exact HA dev runtime when no matching image exists"
        for step in jobs["ha-lifecycle-dev"]["steps"]
    )
    assert "ha-lifecycle-dev" in jobs["certify"]["needs"]
    dev_steps = jobs["ha-lifecycle-dev"]["steps"]
    for name in (
        "Select only an exact immutable HA dev image",
        "Run lifecycle contract in the matching prebuilt runtime",
    ):
        environment = next(
            step["env"] for step in dev_steps if step.get("name") == name
        )
        assert environment["EXPECTED_HA_CORE_SHA"] == "${{ steps.python.outputs.sha }}"
        assert (
            environment["EXPECTED_PYTHON_VERSION"]
            == "${{ steps.python.outputs.version }}"
        )
    runtime = (ROOT / "ci/check_ha_dev_runtime.sh").read_text()
    assert 'test "$IMAGE_SHA" = "$EXPECTED_HA_CORE_SHA"' in runtime
    assert 'test "$IMAGE_PYTHON" = "$EXPECTED_PYTHON_VERSION"' in runtime
    assert "--check /opt/eoai-ci/environment.identity.json" in runtime

    engines = jobs["browser-engines"]
    assert engines["strategy"]["matrix"]["engine"] == ["firefox", "webkit"]
    assert "browser-${{ matrix.engine }}" in engines["container"]["image"]
    assert any(
        step.get("name") == "Verify prebuilt Playwright engine"
        for step in engines["steps"]
    )
    expose_playwright = next(
        step["run"]
        for step in engines["steps"]
        if step.get("name") == "Expose prebuilt Playwright packages to the checkout"
    )
    assert "ln -s" in expose_playwright
    assert 'import("@playwright/test")' in expose_playwright
    assert (
        'npm install --global "@playwright/test@${PLAYWRIGHT_VERSION}"'
        in Path("ci/Dockerfile.stable").read_text()
    )
    stable_images = workflow("ci-image-stable.yml")["jobs"]["build"]["steps"]
    assert any(
        step.get("name") == "Build and publish Firefox nightly image"
        for step in stable_images
    )
    assert any(
        step.get("name") == "Build and publish WebKit nightly image"
        for step in stable_images
    )
    stable_reconciler = Path("ci/reconcile_stable_environment.sh").read_text()
    assert "environment.identity.json" in stable_reconciler
    assert "sha256sum" in stable_reconciler
    assert "EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION" in stable_reconciler
    assert (
        "pytest-homeassistant-custom-component==${EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION"
        in stable_reconciler
    )
    stable_image_workflow = workflow("ci-image-stable.yml")["jobs"]["build"]
    assert any(
        step.get("name") == "Resolve stable Home Assistant and compatible test plugin"
        for step in stable_image_workflow["steps"]
    )
    stable_dockerfile = Path("ci/Dockerfile.stable").read_text()
    assert (
        "pytest-homeassistant-custom-component==${HA_TEST_PLUGIN_VERSION}"
        in stable_dockerfile
    )
    assert "homeassistant==${HOMEASSISTANT_VERSION}" in stable_dockerfile

    dev_image_verify = next(
        step["run"]
        for step in workflow("ci-image-dev.yml")["jobs"]["build"]["steps"]
        if step.get("name") == "Verify published image"
    )
    assert 'if [[ "$GITHUB_EVENT_NAME" != pull_request ]]' in dev_image_verify
    assert "check_ha_dev_runtime.sh" in dev_image_verify
    assert 'docker pull "${IMAGE_NAME}:typecheck-' in dev_image_verify
    assert 'docker pull "${IMAGE_NAME}:ha-dev"' in dev_image_verify

    persistence_job = jobs["persistence_runtime"]
    assert persistence_job["needs"] == "prepare"
    assert "HISTORICAL_RELEASE_SHA" in persistence_job["env"]
    assert all(
        "${{ runner." not in str(value) for value in persistence_job["env"].values()
    )
    assert any(
        step.get("uses", "").startswith("actions/cache@")
        for step in persistence_job["steps"]
    )
    historical_runtime = Path("ci/prepare_historical_runtime.sh").read_text()
    assert 'python -m venv "$HISTORICAL_RUNTIME_DIR"' in historical_runtime
    assert "homeassistant==$HISTORICAL_HA_VERSION" in historical_runtime
    persistence_campaign = jobs["python-campaigns"]
    assert persistence_campaign["needs"] == ["prepare", "persistence_runtime"]
    assert "always()" in persistence_campaign["if"]
    assert "Verify or safely build the historical runtime" in [
        step.get("name") for step in persistence_campaign["steps"]
    ]

    dev_runner = Path("ci/run_prebuilt_ha_dev.sh").read_text()
    assert '[[ -z "$BASE_IMAGE" ]]' in dev_runner
    assert "this legacy image will be reconciled on each run" in dev_runner

    assert jobs["prepare"]["steps"]
    schedule_campaigns = next(
        step
        for step in jobs["prepare"]["steps"]
        if step.get("name") == "Resolve reproducible run controls"
    )
    assert 'intensities=["normal","heavy"]' in schedule_campaigns["run"]
