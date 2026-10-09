"""Keep deployment-recovery acceptance source-bound and genuinely HTTPS/native."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/deployment-recovery.yml").read_text(
            encoding="utf-8"
        )
    )


def test_deployment_recovery_checks_out_exact_candidate_and_runs_both_boundaries():
    job = _workflow()["jobs"]["deployment-recovery"]
    checkout = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    run = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Run deployment recovery acceptance"
    )
    assert "tests_real_ha/test_native_ha_backup_restore.py" in run
    assert "tests_real_ha/test_https_proxy_acceptance.py" in run


def test_deployment_recovery_uploads_fail_closed_candidate_evidence():
    job = _workflow()["jobs"]["deployment-recovery"]
    evidence = next(
        step
        for step in job["steps"]
        if step.get("with", {}).get("name") == "deployment-recovery-evidence"
    )
    assert evidence["with"]["if-no-files-found"] == "error"
    record = next(
        step
        for step in job["steps"]
        if step.get("name") == "Record source-bound deployment evidence"
    )
    assert '"candidate_sha": os.environ["CANDIDATE_SHA"]' in record["run"]
    assert '"native_whole_installation_backup_restore": True' in record["run"]
    assert '"https_reverse_proxy_browser": True' in record["run"]


def test_https_browser_config_is_isolated_and_accepts_only_test_certificate():
    config = (
        ROOT / "playwright.real-ha-https-proxy.config.mjs"
    ).read_text(encoding="utf-8")
    assert 'testMatch: ["real-ha-https-proxy.spec.mjs"]' in config
    assert "ignoreHTTPSErrors: true" in config
    ordinary = (ROOT / "playwright.real-ha-shell.config.mjs").read_text(
        encoding="utf-8"
    )
    assert "real-ha-https-proxy.spec.mjs" not in ordinary
