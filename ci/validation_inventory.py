"""Reviewed complete, non-live release candidate validation inventory."""

# These workflows own their environments and test selection. Keep this list in
# sync with test-bearing workflow_dispatch workflows; live API acceptance,
# release publishing, image publishing, supplementary race amplification,
# and maintenance are separate from this validation run.
TEST_WORKFLOWS = (
    ("ci.yml", {}),
    ("post-merge-smoke.yml", {}),
    ("frontend.yml", {}),
    ("real-ha.yml", {}),
    ("enhanced-stress.yml", {"campaign": "all", "intensity": "heavy"}),
    ("release-smoke.yml", {}),
    ("cross-browser-smoke.yml", {}),
    ("upgrade-acceptance.yml", {"from_version": "all"}),
    ("frontend-backend-version-skew.yml", {}),
    ("ha-version-upgrade-acceptance.yml", {}),
    ("hacs-install-update-acceptance.yml", {}),
    ("deployment-recovery.yml", {}),
    ("side-by-side-isolation.yml", {}),
    ("ipv6-only-networking-acceptance.yml", {}),
    ("ha-browser-compatibility.yml", {}),
    ("android-companion-app.yml", {}),
    ("ios-companion-app.yml", {}),
    ("mutation.yml", {"campaign": "all"}),
    ("hacs.yaml", {}),
    ("frontend-latency-diagnostics.yml", {"runs": "3", "diagnostic_picker": "false"}),
    ("haos-supervisor-vm-acceptance.yml", {}),
    ("official-ha-container.yml", {}),
    ("resource-constrained.yml", {}),
    ("deployment-architecture.yml", {}),
    ("docs.yml", {}),
    ("openai-sdk-compatibility.yml", {}),
    ("version-check.yml", {}),
)
CERTIFICATION_WORKFLOW = "nightly-programme.yml"
