"""Reviewed, non-default backup fixture for the persistent agent contract.

The exceptions are intentionally explicit: a schema version has one supported
value, Skills require an installed skill, and ``memory_auto_create`` is derived
from the selected Memory mode (manual keeps the public wire probe local).
"""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses import agent_config

FIXTURE_EXCEPTIONS = {
    "guest_policy_version": "only the current schema version validates",
    "skills": "requires an installed skill outside this hermetic HA fixture",
    "memory_auto_create": "derived from memory_mode; manual avoids provider writes",
}

_TOOL = {
    "spec": {
        "name": "backup_marker",
        "description": "Return the backup marker",
        "parameters": {"type": "object", "properties": {}},
    },
    "function": {"type": "template", "value_template": "BACKUP-TOOL-MARKER"},
    "enabled": True,
}

FIXTURE_OVERRIDES = {
    "advanced_options": True,
    "api_mode": "chat_completions",
    "archive_enabled": True,
    "archive_model_search_enabled": True,
    "archive_retention_days": 7,
    "archive_session_timeout_minutes": 60,
    "chat_model": "gpt-5.6",
    "context_threshold": 28000,
    "context_truncate_strategy": "clear",
    "continue_conversation": "always",
    "conversation_continuity": "user",
    "conversation_timeout_minutes": 60,
    "current_datetime_enabled": False,
    "current_datetime_template": "Date marker: {{ now().year }}",
    "exposed_entities_enabled": False,
    "exposed_entities_template": "Devices: {{ exposed_entities | length }}",
    "exposed_entity_attributes": {"registry:fixture-entity": ["brightness"]},
    "function_groups": [
        {
            "id": "backup-group",
            "name": "Backup group",
            "description": "Backup tool",
            "loading_mode": "on_demand",
            "functions": ["backup_marker"],
            "enabled": True,
        }
    ],
    "function_tools": [_TOOL],
    "function_tool_error_recovery": True,
    "guest_allowed_function_names": ["backup_marker"],
    "guest_allowed_group_ids": ["backup-group"],
    "guest_controllable_areas": ["private-area"],
    "guest_controllable_domains": ["light"],
    "guest_controllable_entities": ["light.private"],
    "guest_controllable_labels": ["private-label"],
    "guest_control_excluded_areas": ["private-area"],
    "guest_control_excluded_domains": ["lock"],
    "guest_control_excluded_entities": ["light.private"],
    "guest_control_excluded_labels": ["private-label"],
    "guest_excluded_areas": ["private-area"],
    "guest_excluded_domains": ["lock"],
    "guest_excluded_entities": ["light.private"],
    "guest_excluded_labels": ["private-label"],
    "guest_function_policy": "custom",
    "guest_knowledge_enabled": True,
    "guest_knowledge_policy": "custom",
    "guest_knowledge_source_ids": ["fixture-source"],
    "guest_mode_enabled": False,
    "guest_readable_areas": ["shared-area"],
    "guest_readable_domains": ["light"],
    "guest_readable_entities": ["light.shared"],
    "guest_readable_labels": ["shared-label"],
    "guest_separate_control_restrictions": True,
    "guest_shared_memory_policy": "read_only",
    "guest_shared_memory_read": True,
    "guest_shared_memory_write": True,
    "guest_web_search": True,
    "knowledge_enabled": True,
    "local_intents_enabled": False,
    "local_intent_delayed_commands_to_ai": True,
    "local_intent_exclusions": ["HassTurnOn"],
    "max_function_calls_per_conversation": 8,
    "max_tokens": 900,
    "memory_auto_retrieve_limit": 2,
    "memory_embedding_model": "text-embedding-3-large",
    "memory_enabled": True,
    "memory_mode": "manual",
    "memory_retrieval_mode": "hybrid",
    "prompt": "Preserve café 🎯 and answer in English.",
    "reasoning_effort": "medium",
    "service_tier": "auto",
    "shared_archive_enabled": True,
    "shared_memory_mode": "explicit",
    "shorten_tool_call_id": True,
    "speech_processing_enabled": True,
    "speech_regex_replacements": [{"pattern": "alpha", "replacement": "beta"}],
    "speech_strip_markdown": False,
    "speech_strip_urls": False,
    "temperature": 0.3,
    "temporary_memory": "balanced",
    "top_p": 0.8,
    "usage_request_retention_days": 7,
    "usage_run_retention_days": 30,
    "voice_default_user_id": "backup-owner",
    "voice_device_mappings": {"fixture-satellite": "user:backup-owner"},
    "voice_scope_policy": "device_mapping",
    "voice_unmapped_policy": "shared",
    "web_search": True,
    "web_search_context": "medium",
}


def maximal_agent_options() -> dict:
    """Validate and return the maximal config, failing on every new field."""
    defaults = agent_config.agent_config_defaults()
    assert set(FIXTURE_OVERRIDES) | set(FIXTURE_EXCEPTIONS) == set(
        agent_config.AGENT_CONFIG_FIELDS
    ), "Classify every persistent field in the maximal backup fixture"
    for key, value in FIXTURE_OVERRIDES.items():
        assert value != defaults[key], f"{key} must exercise a non-default value"
    normalized = agent_config.normalize_agent_config(FIXTURE_OVERRIDES)
    for key in FIXTURE_OVERRIDES:
        assert normalized[key] != defaults[key], f"{key} normalized to its default"
    return normalized
