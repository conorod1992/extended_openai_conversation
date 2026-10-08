"""Release selection must be bound to one exact, completely certified SHA."""

from copy import deepcopy
import json

import pytest

from ci.enhanced_evidence import SCHEMA, environment_fingerprint
from ci.execution_contract import CONTRACT, expected_cases
from ci.release_certification import (
    ADVISORY_SDK_LANE,
    HEAVY_CAMPAIGNS,
    HEAVY_JOBS,
    MUTATION_CAMPAIGNS,
    REQUIRED_WORKFLOWS,
    SCHEDULED_CERTIFICATION_WORKFLOWS,
    SPECIALIST_CERTIFICATION_WORKFLOWS,
    SUPPORTED_SDK_LANES,
    UPGRADE_EPOCHS,
    certify,
)

SOURCE = "b" * 40
PARENT = "a" * 40


class FakeActions:
    def __init__(self):
        self.runs = {}
        self.jobs = {}
        self.artifacts = {}
        self.contents = {}
        for index, filename in enumerate(REQUIRED_WORKFLOWS, 1):
            self.runs[filename] = [
                {
                    "id": index,
                    "workflow_id": index,
                    "head_sha": SOURCE,
                    "head_branch": "develop",
                    "event": "workflow_dispatch"
                    if filename == "enhanced-stress.yml"
                    or filename in SCHEDULED_CERTIFICATION_WORKFLOWS
                    else "push",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": f"https://example.test/actions/runs/{index}",
                }
            ]
            self.jobs[index] = [
                {"name": name, "conclusion": "success"} for name in HEAVY_JOBS
            ]

            self.artifacts[index] = []
            if filename == "enhanced-stress.yml":
                self._artifact(index, "certification-index-123", self._nightly())
            elif filename in {"upgrade-acceptance.yml", "openai-sdk-compatibility.yml"}:
                kind = "upgrade" if filename == "upgrade-acceptance.yml" else "sdk"
                lanes = (
                    UPGRADE_EPOCHS
                    if kind == "upgrade"
                    else (*SUPPORTED_SDK_LANES, ADVISORY_SDK_LANE)
                )
                self.jobs[index] = []
                for lane in lanes:
                    name = (
                        f"{lane} to develop"
                        if kind == "upgrade"
                        else f"SDK wire contract / {lane}"
                    )
                    self.jobs[index].append({"name": name, "conclusion": "success"})
                    self._artifact(
                        index,
                        f"{kind}-evidence-{lane}",
                        {
                            **self._envelope(
                                "release-" + kind,
                                sdk=lane if kind == "sdk" else "3.10.0",
                            ),
                            "workflow_kind": kind,
                            "lane": lane,
                            "source_version": "6.8.4" if lane == "latest" else lane,
                            "upgrade_source_sha": PARENT,
                        },
                    )
            elif filename == "mutation.yml":
                self.jobs[index] = [
                    {
                        "name": f"Mutation campaign ({campaign})",
                        "conclusion": "success",
                    }
                    for campaign in MUTATION_CAMPAIGNS
                ]
            elif filename == "official-ha-container.yml":
                self.jobs[index] = []
                for arch, machine, image_arch in (
                    ("amd64", "x86_64", "amd64"),
                    ("arm64", "aarch64", "arm64"),
                ):
                    self.jobs[index].append(
                        {
                            "name": f"Official HA Container runtime ({arch})",
                            "conclusion": "success",
                        }
                    )
                    self._artifact(
                        index,
                        f"official-ha-container-{arch}-evidence",
                        {
                            "candidate_sha": SOURCE,
                            "passed": True,
                            "official_container": True,
                            "homeassistant": "2026.9.4",
                            "image_id": "sha256:" + "c" * 64,
                            "machine": machine,
                            "container_machine": machine,
                            "image_architecture": image_arch,
                            "phases": ["seed", "recover", "entrypoint"],
                            "retained_entry": True,
                            "retained_entities": True,
                        },
                    )

            elif filename in SPECIALIST_CERTIFICATION_WORKFLOWS:
                from ci.specialist_evidence import REQUIRED_SOURCES
                self.runs[filename][0]["run_attempt"] = 1
                self.jobs[index] = [{"conclusion": "success", "steps": [{"name": "Record specialist execution evidence", "conclusion": "success"}]}]
                cases = [f"{source}.test_acceptance" for source in REQUIRED_SOURCES[filename]] or ["companion-app-smoke"]
                if filename == "haos-supervisor-vm-acceptance.yml":
                    cases = ["supervisor_core_restart", "supervisor_full_backup_restore_reboot"]
                self._artifact(index, "specialist-execution-evidence", {
                    "candidate_sha": SOURCE, "workflow": filename, "passed": True,
                    "run_id": index, "run_attempt": 1, "executed_cases": cases,
                })

    def _envelope(self, campaign, *, sdk="3.10.0"):
        identity = {
            "python": "3.14.0",
            "packages": {"homeassistant": "2026.9.4", "openai": sdk},
        }
        return {
            "schema": SCHEMA,
            "eoai_sha": SOURCE,
            "campaign": campaign,
            "status": "success",
            "environment": identity,
            "environment_fingerprint": environment_fingerprint(identity),
        }

    def _nightly(self):
        policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
        groups = [
            (campaign, "heavy", None)
            for campaign in (
                *HEAVY_CAMPAIGNS,
                "browser",
                "browser-firefox",
                "browser-webkit",
            )
        ]
        groups += [
            ("lifecycle-matrix", "normal", point)
            for point in ("oldest", "stable", "dev")
        ]
        jobs = []
        for campaign, intensity, point in groups:
            item = self._envelope(campaign)
            item.update(
                intensity=intensity,
                ha_point=point,
                execution_runs=[
                    {
                        **self._envelope(campaign),
                        "execution_id": "probe",
                        "runner": "pytest",
                    }
                ],
                execution_cases=[
                    {
                        "nodeid": node,
                        "collected": True,
                        "executed": True,
                        "outcome": "passed",
                        "execution_id": "probe",
                    }
                    for node in sorted(expected_cases(policy, campaign))
                ],
                measured_totals=policy.get("minimums", {}).get(campaign, {}),
            )
            jobs.append(item)
        return {
            "schema": SCHEMA,
            "candidate_sha": SOURCE,
            "selected": "all",
            "passed": True,
            "execution_errors": [],
            "identity_errors": [],
            "jobs": jobs,
        }

    def _artifact(self, run_id, name, contents):
        artifact_id = run_id * 100 + len(self.artifacts[run_id])
        self.artifacts[run_id].append(
            {"id": artifact_id, "name": name, "expired": False}
        )
        self.contents[artifact_id] = contents

    def run_artifacts(self, run_id):
        return self.artifacts[run_id]

    def artifact_json(self, artifact, filename):
        assert filename in {
            "workflow-evidence.json",
            "certification-final.json",
            "certification.json",
            "specialist-execution.json",
        }
        return deepcopy(self.contents[artifact["id"]])

    def workflow_runs(self, filename, source_sha):
        assert source_sha == SOURCE
        return REQUIRED_WORKFLOWS.index(filename) + 1, self.runs[filename]

    def run_jobs(self, run_id):
        return self.jobs[run_id]


def test_exact_sha_with_all_certifications_passes():
    actions = FakeActions()
    assert set(certify(actions, SOURCE)) == set(REQUIRED_WORKFLOWS)


def test_passing_parent_sha_cannot_certify_source():
    actions = FakeActions()
    for runs in actions.runs.values():
        runs[0]["head_sha"] = PARENT
    with pytest.raises(RuntimeError, match=f"Release source {SOURCE} is not certified"):
        certify(actions, SOURCE)


@pytest.mark.parametrize(
    "nightly_change",
    [
        "missing",
        "normal",
        "failed",
        "diagnostics",
        "cancelled_job",
    ],
)
def test_ordinary_ci_does_not_replace_complete_heavy_nightly(nightly_change):
    actions = FakeActions()
    nightly = actions.runs["enhanced-stress.yml"]
    if nightly_change == "missing":
        nightly.clear()
    elif nightly_change == "normal":
        actions.jobs[6] = [
            {"name": name.replace(" / heavy", " / normal"), "conclusion": "success"}
            for name in HEAVY_JOBS
        ]
    elif nightly_change == "failed":
        nightly[0]["conclusion"] = "failure"
    elif nightly_change == "diagnostics":
        actions.jobs[6] = [{"name": "diagnostics / heavy", "conclusion": "success"}]
    else:
        actions.jobs[6][0]["conclusion"] = "cancelled"
    with pytest.raises(RuntimeError, match=r"enhanced-stress\.yml"):
        certify(actions, SOURCE)


def test_unrelated_workflow_and_cancelled_or_skipped_runs_do_not_count():
    actions = FakeActions()
    actions.runs["ci.yml"][0]["workflow_id"] = 999
    actions.runs["ci.yml"].append(
        {**actions.runs["ci.yml"][0], "workflow_id": 1, "conclusion": "cancelled"}
    )
    actions.runs["ci.yml"].append(
        {**actions.runs["ci.yml"][0], "workflow_id": 1, "conclusion": "skipped"}
    )
    with pytest.raises(RuntimeError, match=r"ci\.yml"):
        certify(actions, SOURCE)


def test_any_complete_successful_heavy_run_is_sufficient():
    actions = FakeActions()
    failed = {**actions.runs["enhanced-stress.yml"][0], "conclusion": "failure"}
    actions.runs["enhanced-stress.yml"].insert(0, failed)
    assert "enhanced-stress.yml" in certify(actions, SOURCE)


def test_unavailable_workflow_metadata_fails_with_source_and_expected_check():
    actions = FakeActions()

    def unavailable(filename, source_sha):
        if filename == "frontend.yml":
            raise OSError("Actions API unavailable")
        return FakeActions.workflow_runs(actions, filename, source_sha)

    actions.workflow_runs = unavailable
    with pytest.raises(
        RuntimeError, match=f"Release source {SOURCE} is not certified"
    ) as error:
        certify(actions, SOURCE)
    assert "frontend.yml: expected successful workflow run" in str(error.value)


@pytest.mark.parametrize("other", [PARENT, "c" * 40])
def test_ancestor_and_descendant_never_replace_candidate(other):
    actions = FakeActions()
    actions.runs["upgrade-acceptance.yml"][0]["head_sha"] = other
    with pytest.raises(RuntimeError, match="upgrade-acceptance"):
        certify(actions, SOURCE)


def test_same_sha_on_other_branch_does_not_certify_release_policy():
    actions = FakeActions()
    actions.runs["ci.yml"][0]["head_branch"] = "another-branch"
    with pytest.raises(RuntimeError, match=r"ci\.yml"):
        certify(actions, SOURCE)


def test_latest_only_upgrade_cannot_replace_historical_epochs():
    actions = FakeActions()
    actions.jobs[7] = [actions.jobs[7][0]]
    with pytest.raises(RuntimeError, match=r"6\.2\.0 to develop"):
        certify(actions, SOURCE)


@pytest.mark.parametrize("change", ["missing", "failed", "wrong_sdk", "wrong_sha"])
def test_supported_sdk_lane_requires_exact_successful_environment(change):
    actions = FakeActions()
    if change == "missing":
        actions.artifacts[8].pop(0)
    elif change == "failed":
        actions.jobs[8][0]["conclusion"] = "failure"
    else:
        item = actions.contents[actions.artifacts[8][0]["id"]]
        if change == "wrong_sha":
            item["eoai_sha"] = PARENT
        else:
            item["environment"]["packages"]["openai"] = "9.9.9"
            item["environment_fingerprint"] = environment_fingerprint(
                item["environment"]
            )
    with pytest.raises(RuntimeError, match="openai-sdk-compatibility"):
        certify(actions, SOURCE)


def test_advisory_sdk_failure_does_not_replace_or_block_supported_lanes():
    actions = FakeActions()
    actions.jobs[8][-1]["conclusion"] = "failure"
    actions.artifacts[8].pop()
    assert certify(actions, SOURCE)
    actions.jobs[8].pop(0)
    with pytest.raises(RuntimeError, match=r"SDK wire contract / 2\.21\.0"):
        certify(actions, SOURCE)


@pytest.mark.parametrize(
    "change",
    [
        "candidate",
        "all_jobs",
        "one_job",
        "skipped_case",
        "missing_artifact",
        "missing_heavy",
    ],
)
def test_release_rechecks_exact_enhanced_execution_artifact(change):
    actions = FakeActions()
    index = actions.contents[actions.artifacts[6][0]["id"]]
    if change == "missing_artifact":
        actions.artifacts[6].clear()
    elif change == "candidate":
        index["candidate_sha"] = PARENT
    elif change in {"all_jobs", "one_job"}:
        for item in index["jobs"] if change == "all_jobs" else index["jobs"][:1]:
            item["eoai_sha"] = PARENT
            item["execution_runs"][0]["eoai_sha"] = PARENT
    elif change == "skipped_case":
        index["jobs"][0]["execution_cases"][0]["outcome"] = "skipped"
    else:
        index["jobs"].pop(0)
    with pytest.raises(RuntimeError, match="enhanced-stress"):
        certify(actions, SOURCE)


def test_upgrade_envelope_must_name_the_actual_reviewed_source_epoch():
    actions = FakeActions()
    item = actions.contents[actions.artifacts[7][-1]["id"]]
    item["source_version"] = "6.8.4"
    with pytest.raises(RuntimeError, match="matching released payload"):
        certify(actions, SOURCE)


def test_release_requires_every_mutation_campaign():
    actions = FakeActions()
    run_id = REQUIRED_WORKFLOWS.index("mutation.yml") + 1
    actions.jobs[run_id].pop()
    with pytest.raises(RuntimeError, match="Required mutation campaigns absent"):
        certify(actions, SOURCE)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "missing_lane",
        "candidate",
        "passed",
        "phases",
        "retention",
        "identity",
        "architecture",
    ],
)
def test_official_container_evidence_is_source_bound_and_complete(change):
    actions = FakeActions()
    run_id = REQUIRED_WORKFLOWS.index("official-ha-container.yml") + 1
    artifact = actions.artifacts[run_id][0]
    proof = actions.contents[artifact["id"]]
    if change == "missing":
        actions.artifacts[run_id].clear()
    elif change == "missing_lane":
        actions.jobs[run_id] = [
            job
            for job in actions.jobs[run_id]
            if job["name"] != "Official HA Container runtime (arm64)"
        ]
    elif change == "candidate":
        proof["candidate_sha"] = PARENT
    elif change == "passed":
        proof["passed"] = False
    elif change == "phases":
        proof["phases"].remove("entrypoint")
    elif change == "retention":
        proof["retained_entry"] = False
    elif change == "architecture":
        proof["container_machine"] = "wrong-arch"
    else:
        proof["image_id"] = ""
    with pytest.raises(RuntimeError, match="official-ha-container"):
        certify(actions, SOURCE)


def test_all_new_certification_workflows_are_exact_sha_requirements():
    required = {
        "ha-browser-compatibility.yml",
        "deployment-architecture.yml",
        "resource-constrained.yml",
        "mutation.yml",
        "official-ha-container.yml",
    }
    assert required <= set(REQUIRED_WORKFLOWS)


def test_signed_artifact_download_never_forwards_github_authorization(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from zipfile import ZipFile

    from ci import release_certification as module

    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        zipped.writestr("workflow-evidence.json", json.dumps({"eoai_sha": SOURCE}))
    archive = buffer.getvalue()
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert request.get_header("Authorization") == "Bearer fixture-token"
            raise HTTPError(
                request.full_url,
                302,
                "Found",
                {
                    "Location": "https://signed-blob.example.test/artifact?signature=fixture"
                },
                BytesIO(),
            )

    def signed_download(request, timeout):
        requests.append(request)
        assert request.get_header("Authorization") is None
        return BytesIO(archive)

    monkeypatch.setattr(module, "build_opener", lambda _: Opener())
    monkeypatch.setattr(module, "urlopen", signed_download)
    actions = module.GitHubActions("owner/repo", "fixture-token")
    assert actions.artifact_json(
        {"id": 1, "name": "sdk-evidence", "expired": False}, "workflow-evidence.json"
    ) == {"eoai_sha": SOURCE}
    assert len(requests) == 2


def test_expired_matrix_artifact_fails_before_network_access():
    from ci.release_certification import GitHubActions

    with pytest.raises(RuntimeError, match="expired"):
        GitHubActions("owner/repo", "fixture-token").artifact_json(
            {"id": 1, "name": "sdk-evidence", "expired": True}, "workflow-evidence.json"
        )


def test_specialist_environment_workflows_are_exact_sha_release_requirements():
    expected = {
        "android-companion-app.yml",
        "haos-supervisor-vm-acceptance.yml",
        "hacs-install-update-acceptance.yml",
        "ipv6-only-networking-acceptance.yml",
        "frontend-backend-version-skew.yml",
        "ha-version-upgrade-acceptance.yml",
        "deployment-recovery.yml",
        "side-by-side-isolation.yml",
    }
    assert set(SPECIALIST_CERTIFICATION_WORKFLOWS) == expected
    assert expected <= set(REQUIRED_WORKFLOWS)
    assert expected <= SCHEDULED_CERTIFICATION_WORKFLOWS


@pytest.mark.parametrize("workflow", SPECIALIST_CERTIFICATION_WORKFLOWS)
def test_specialist_release_workflow_requires_success_on_exact_develop_sha(workflow):
    actions = FakeActions()
    assert certify(actions, SOURCE)

    actions.runs[workflow][0]["head_sha"] = PARENT
    with pytest.raises(RuntimeError, match=workflow.replace(".", r"\.")):
        certify(actions, SOURCE)

    actions = FakeActions()
    actions.runs[workflow][0]["head_branch"] = "feature-branch"
    with pytest.raises(RuntimeError, match=workflow.replace(".", r"\.")):
        certify(actions, SOURCE)

    actions = FakeActions()
    actions.runs[workflow][0]["event"] = "pull_request"
    with pytest.raises(RuntimeError, match=workflow.replace(".", r"\.")):
        certify(actions, SOURCE)


@pytest.mark.parametrize("workflow", SPECIALIST_CERTIFICATION_WORKFLOWS)
def test_missing_or_failed_specialist_workflow_blocks_release(workflow):
    actions = FakeActions()
    actions.runs[workflow][0]["conclusion"] = "failure"
    with pytest.raises(RuntimeError, match=workflow.replace(".", r"\.")):
        certify(actions, SOURCE)

    actions = FakeActions()
    actions.runs[workflow] = []
    with pytest.raises(RuntimeError, match=workflow.replace(".", r"\.")):
        certify(actions, SOURCE)


@pytest.mark.parametrize("workflow", SPECIALIST_CERTIFICATION_WORKFLOWS)
@pytest.mark.parametrize("fault", ["missing", "empty", "expired", "skipped-job", "skipped-step", "zero-tests", "wrong-tests", "candidate", "run", "attempt"])
def test_specialist_execution_evidence_is_mandatory(workflow, fault):
    actions = FakeActions()
    run_id = actions.runs[workflow][0]["id"]
    artifact = actions.artifacts[run_id][0]
    item = actions.contents[artifact["id"]]
    if fault == "missing": actions.artifacts[run_id] = []
    elif fault == "empty": artifact["size_in_bytes"] = 0
    elif fault == "expired": artifact["expired"] = True
    elif fault == "skipped-job": actions.jobs[run_id][0]["conclusion"] = "skipped"
    elif fault == "skipped-step": actions.jobs[run_id][0]["steps"][0]["conclusion"] = "skipped"
    elif fault == "zero-tests": item["executed_cases"] = []
    elif fault == "wrong-tests":
        if workflow == "android-companion-app.yml": item["executed_cases"] = []
        else: item["executed_cases"] = ["unrelated.test"]
    elif fault == "candidate": item["candidate_sha"] = PARENT
    elif fault == "run": item["run_id"] = 99999
    else: item["run_attempt"] = 99999
    with pytest.raises(RuntimeError, match=workflow): certify(actions, SOURCE)


@pytest.mark.parametrize("xml", ["<testsuites/>", '<testsuite><testcase name="skipped"><skipped/></testcase></testsuite>', '<testsuite><testcase name="failed"><failure/></testcase></testsuite>'])
def test_specialist_report_rejects_zero_execution(tmp_path, xml):
    from ci.specialist_evidence import execution_cases
    path = tmp_path / "report.xml"
    path.write_text(xml)
    if "skipped" in xml:
        assert execution_cases(path) == []
    else:
        with pytest.raises(ValueError): execution_cases(path)


def test_specialist_report_records_only_executed_cases(tmp_path):
    from ci.specialist_evidence import execution_cases
    path = tmp_path / "report.xml"
    path.write_text('<testsuites><testsuite><testcase classname="tests_real_ha.test_acceptance" name="public_path"/><testcase name="skipped"><skipped/></testcase></testsuite></testsuites>')
    assert execution_cases(path) == ["tests_real_ha.test_acceptance.public_path"]


@pytest.mark.parametrize("workflow", SPECIALIST_CERTIFICATION_WORKFLOWS)
def test_specialist_producer_emits_candidate_bound_executed_report(tmp_path, monkeypatch, workflow):
    import sys
    from ci import specialist_evidence as producer

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CANDIDATE_SHA", SOURCE)
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(producer.subprocess, "check_output", lambda *a, **k: SOURCE)
    if workflow == "haos-supervisor-vm-acceptance.yml":
        report = tmp_path / "proof.json"
        report.write_text(json.dumps({"candidate_sha": SOURCE, "provider_calls": 5, "tool_marker": "verified", "supervisor_core_restart": True, "supervisor_full_backup_restore_reboot": True}))
        args = ["--haos-proof", str(report)]
    else:
        report = tmp_path / "tests.xml"
        report.write_text('<testsuite>' + ''.join(f'<testcase classname="acceptance" name="{name}"/>' for name in producer.REQUIRED_SOURCES[workflow]) + '</testsuite>')
        args = ["--report", str(report)]
    monkeypatch.setattr(sys, "argv", ["producer", "--workflow", workflow, *args])
    producer.main()
    item = json.loads((tmp_path / "specialist-execution.json").read_text())
    assert not producer.specialist_errors(item, workflow, SOURCE, {"id": 42, "run_attempt": 2})
    assert producer.specialist_errors(item, workflow, PARENT, {"id": 42, "run_attempt": 2})
