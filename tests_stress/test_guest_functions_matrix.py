"""Guest policy combinations and Function capability inventory."""

from __future__ import annotations

from itertools import product
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_ALLOWED_FUNCTION_NAMES,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_KNOWLEDGE_POLICY,
    CONF_GUEST_KNOWLEDGE_SOURCE_IDS,
    CONF_GUEST_POLICY_VERSION,
    CONF_GUEST_SHARED_MEMORY_POLICY,
    FUNCTION_GROUP_LOADING_MODES,
    GUEST_ACCESS_POLICIES,
    GUEST_POLICY_VERSION,
    GUEST_SHARED_MEMORY_POLICIES,
)
from custom_components.extended_openai_conversation_responses.functions import FUNCTIONS
from custom_components.extended_openai_conversation_responses.guest_mode import (
    resolve_guest_policy,
)
from homeassistant.core import HomeAssistant
from tests_stress.conftest import record

CLASSIFIED_FUNCTION_TYPES = {
    "native",
    "script",
    "template",
    "rest",
    "scrape",
    "composite",
    "sqlite",
    "bash",
    "read_file",
    "write_file",
    "edit_file",
}


def test_new_function_or_guest_mode_requires_classification() -> None:
    assert set(FUNCTIONS) == CLASSIFIED_FUNCTION_TYPES, (
        "Review new Function type in nightly security matrix"
    )
    assert set(FUNCTION_GROUP_LOADING_MODES) == {"always", "on_demand"}
    assert set(GUEST_ACCESS_POLICIES) == {"off", "on", "custom"}
    assert set(GUEST_SHARED_MEMORY_POLICIES) == {"off", "read_only", "read_write"}


def test_guest_policy_matrix_never_grants_unsafe_function_or_private_memory(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    safe = {
        "spec": {"name": "safe_history"},
        "function": {"type": "native", "name": "get_history"},
    }
    unsafe = {"spec": {"name": "unsafe_template"}, "function": {"type": "template"}}
    active = SimpleNamespace(is_active=lambda: True)
    combinations = 0
    for function_policy, knowledge_policy, memory_policy in product(
        GUEST_ACCESS_POLICIES,
        GUEST_ACCESS_POLICIES,
        GUEST_SHARED_MEMORY_POLICIES,
    ):
        options = {
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_FUNCTION_POLICY: function_policy,
            CONF_GUEST_ALLOWED_FUNCTION_NAMES: ["safe_history", "unsafe_template"],
            CONF_GUEST_KNOWLEDGE_POLICY: knowledge_policy,
            CONF_GUEST_KNOWLEDGE_SOURCE_IDS: ["source-a"],
            CONF_GUEST_SHARED_MEMORY_POLICY: memory_policy,
        }
        policy = resolve_guest_policy(
            hass,
            options,
            active,
            [safe, unsafe],
            exposed_entities=[],
        )
        assert policy.guest_active
        assert "unsafe_template" not in policy.configured_tool_names
        assert ("safe_history" in policy.configured_tool_names) == (
            function_policy != "off"
        )
        assert not policy.personal_memory_read and not policy.personal_memory_write
        assert not policy.temporary_memory
        assert policy.shared_memory_read == (memory_policy != "off")
        assert policy.shared_memory_write == (memory_policy == "read_write")
        assert policy.knowledge_access == (knowledge_policy != "off")
        assert policy.readable_entity_ids == frozenset()
        combinations += 1
    record(
        stress_trace,
        "summary",
        guest_policy_combinations=combinations,
        function_types=len(FUNCTIONS),
    )
