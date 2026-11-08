"""Retention must never remove source-bound acceptance evidence."""

import json
from unittest.mock import Mock

from ci import ios_companion_build as native


def test_retention_deletes_only_old_reproducible_native_products(monkeypatch):
    artifacts = [
        {
            "id": index,
            "name": "ios-native-" + str(index) * 64,
            "created_at": f"2026-11-0{index}",
            "expired": False,
            "workflow_run": {"id": index},
        }
        for index in (1, 2, 3)
    ]
    artifacts.append({"id": 99, "name": "ios-companion-app-evidence", "expired": False})

    def api(endpoint):
        if "artifacts?" in endpoint:
            return {"artifacts": artifacts}
        return {"path": native.WORKFLOW, "head_repository": {"full_name": "owner/repo"}}

    monkeypatch.setattr(native, "gh_json", api)
    delete = Mock()
    monkeypatch.setattr(native.subprocess, "run", delete)
    native.prune_artifacts("owner/repo")
    delete.assert_called_once_with(
        ["gh", "api", "--method", "DELETE", "repos/owner/repo/actions/artifacts/1"],
        check=True,
    )


def test_report_uses_completed_steps_and_preserves_failure_results(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(
        native,
        "gh_json",
        lambda *_: {
            "jobs": [
                {
                    "steps": [
                        {
                            "name": "Build",
                            "started_at": "2026-11-08T00:00:00Z",
                            "completed_at": "2026-11-08T00:02:10Z",
                            "conclusion": "success",
                        },
                        {
                            "name": "Journey",
                            "started_at": "2026-11-08T00:03:00Z",
                            "completed_at": "2026-11-08T00:04:00Z",
                            "conclusion": "failure",
                        },
                        {
                            "name": "Still running",
                            "started_at": "2026-11-08T00:04:00Z",
                            "completed_at": None,
                        },
                    ]
                }
            ]
        },
    )
    native.report_timings("owner/repo", "123")
    rows = json.loads((tmp_path / "ios-companion-timings.json").read_text())
    assert [row["seconds"] for row in rows] == [130, 60]
    assert rows[1]["conclusion"] == "failure"
    assert "| Journey | 60 | failure |" in (tmp_path / "summary").read_text()
