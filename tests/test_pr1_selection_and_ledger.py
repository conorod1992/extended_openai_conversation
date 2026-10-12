"""Regression checks for shared acceptance-helper selection and ownership boundaries."""

import pytest

from ci.pr_validation import select
from tests.resource_lifecycle_ledger import _owned_storage_name


@pytest.mark.parametrize("path", [
    "tests/resource_lifecycle_ledger.py",
    "tests/functions/behaviour_generators.py",
    "tests_stress/test_os_storage_faults.py",
])
def test_shared_acceptance_helper_changes_select_real_ha(path):
    plan = select([path], "develop")
    assert plan["real-ha"] is True
    # A helper edit must not force unrelated deployment or packaging suites.
    assert plan["deployment-recovery"] is False
    assert plan["release-smoke"] is False


@pytest.mark.parametrize("name,owned", [
    ("extended_openai_conversation_responses.memory.entry.agent", True),
    ("extended_openai_conversation_responses.memory.entry.agent.embeddings", True),
    ("extended_openai_conversation_responses.knowledge.entry.agent", True),
    ("extended_openai_conversation_responses.memory.entry.agent2", False),
    ("extended_openai_conversation_responses.memory.entry.agent-2", False),
    ("extended_openai_conversation_responses.memory.other.agent", False),
    ("extended_openai_conversation_responses.memory.entry.other", False),
    ("extended_openai_conversation_responses.memory.entry.agent_extra", False),
])
def test_storage_owner_identity_is_not_a_prefix(name, owned):
    assert _owned_storage_name(name, "entry", "agent") is owned
