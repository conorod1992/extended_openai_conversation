"""Contracts for the side-by-side original/fork identity acceptance lane."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "side-by-side-isolation.yml"
ACCEPTANCE = ROOT / "tests_real_ha" / "test_identity_isolation_matrix.py"

ORIGINAL_RELEASE = "2.0.2"
ORIGINAL_SHA = "ceb3dc224c1d59e526a565818b618daafeae3855"


def test_workflow_pins_real_original_integration_and_runs_acceptance() -> None:
    """The lane must use an immutable upstream source, not a fake original domain."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "jekalmin/extended_openai_conversation.git" in workflow
    assert f'ORIGINAL_RELEASE: "{ORIGINAL_RELEASE}"' in workflow
    assert f'ORIGINAL_SHA: "{ORIGINAL_SHA}"' in workflow
    assert 'test "$(git -C "$ORIGINAL_ROOT" rev-parse HEAD)" = "$ORIGINAL_SHA"' in workflow
    assert "tests_real_ha/test_identity_isolation_matrix.py" in workflow


def test_acceptance_covers_documented_domain_and_generation_boundaries() -> None:
    """Protect the four reviewed identity/isolation obligations from being weakened."""
    source = ACCEPTANCE.read_text(encoding="utf-8")
    required = (
        "test_duplicate_conversation_titles_across_parents_keep_identity",
        "test_duplicate_ai_task_titles_across_parents_route_by_entity_identity",
        "test_deleted_parent_recreated_with_same_names_gets_fresh_generation",
        "test_original_and_fork_run_side_by_side_without_cross_domain_ownership",
        'extended_openai_conversation"',
        "extended_openai_conversation_responses",
        "FORK_SIDE_BY_SIDE_STORAGE_MARKER",
        "ORIGINAL_WORKSPACE_MARKER",
        "FORK_WORKSPACE_MARKER",
        "SERVICE_PROCESS",
        "DATA_PANELS",
    )
    for marker in required:
        assert marker in source, marker
