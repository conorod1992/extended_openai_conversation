"""Contracts for retained-context publication revalidation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONVERSATION = (
    ROOT
    / "custom_components"
    / "extended_openai_conversation_responses"
    / "conversation.py"
)
KNOWLEDGE = (
    ROOT
    / "custom_components"
    / "extended_openai_conversation_responses"
    / "knowledge.py"
)
ACCEPTANCE = ROOT / "tests_real_ha" / "test_retained_context_publication_races.py"


def test_publication_revalidation_remains_on_all_three_retained_context_paths() -> None:
    conversation = CONVERSATION.read_text(encoding="utf-8")
    knowledge = KNOWLEDGE.read_text(encoding="utf-8")
    acceptance = ACCEPTANCE.read_text(encoding="utf-8")

    for marker in (
        "_async_revalidate_retrieved_memories",
        "_revalidate_temporary_memory_expiry",
        "async_revalidate_search_results",
    ):
        assert marker in conversation or marker in knowledge

    for case in (
        "test_memory_edit_during_retrieval_publishes_current_record",
        "test_temporary_memory_expiring_after_prefetch_is_omitted_from_prompt",
        "test_deleted_knowledge_source_after_search_never_reaches_tool_result",
    ):
        assert case in acceptance
