"""Twenty-five reviewed candidate fault injections for EOAI's defect benchmark.

The first four are the existing mandatory contract probes; the remainder are
exploratory and cannot be counted as detected until a clean baseline passes,
a genuinely changed implementation is exercised, and an assertion fails.
All patches apply to disposable git-archive snapshots, never the checkout.
"""
from dataclasses import dataclass

ROOT = "custom_components/extended_openai_conversation_responses/"
HA = "tests_real_ha/"
GENERAL = HA + "test_cross_feature_acceptance.py"


@dataclass(frozen=True)
class Challenge:
    name: str
    family: str
    path: str
    anchor: str
    replacement: str
    dedicated: str
    general: str = GENERAL
    mandatory: bool = False


def C(name, family, file, anchor, replacement, dedicated, general=GENERAL):
    return Challenge(name, family, ROOT + file, anchor, replacement, HA + dedicated, general)


EXPLORATORY = (
    C("guest-owner-collapse", "identity", "conversation_id_ownership.py",
      'f"guest:{scope.scope_id}" if guest_active else scope.scope_id',
      '"guest" if guest_active else scope.scope_id',
      "test_privacy_ownership_remediation.py", HA + "test_identity_isolation_matrix.py"),
    C("continuity-device-owner-drop", "identity", "continuity.py",
      'key = f"{scope.scope_id}:{key}"', 'key = f"device:{device_id}"',
      "test_privacy_ownership_remediation.py", HA + "test_readiness_archive_continuity.py"),
    C("satellite-context-erasure", "continuity", "conversation.py",
      "chat_log.extra_system_prompt = user_input.extra_system_prompt",
      "chat_log.extra_system_prompt = None",
      "test_privacy_ownership_remediation.py", HA + "test_provider_input_history.py"),
    C("whitespace-replays-history", "continuity", "conversation.py",
      "if not user_input.text.strip():", "if user_input.text is None:",
      "test_readiness_archive_continuity.py", HA + "test_cross_feature_acceptance.py"),
    C("memory-add-privacy-bypass", "memory", "memory.py",
      "_validate_privacy_fields(source, content, category, subject, key)",
      "pass  # deliberate metadata/content validation bypass",
      "test_memory_provider_wire_e2e.py", HA + "test_user_ownership_privacy.py"),
    C("memory-upsert-provenance-bypass", "memory", "memory.py",
      'current = self._memories[memory_id]\\n                if current.source == "explicit" and source == "implicit":',
      'current = self._memories[memory_id]\\n                if False:  # deliberate implicit overwrite',
      "test_memory_provider_wire_e2e.py", HA + "test_persistent_memory_runtime_recovery.py"),
    C("temporary-memory-metadata-bypass", "memory", "temporary_memory.py",
      'validate_memory_privacy(category, automatic=source == "automatic")',
      "pass  # deliberate category privacy bypass",
      "test_temporary_memory_lifecycle.py", HA + "test_memory_provider_wire_e2e.py"),
    C("temporary-memory-update-bypass", "memory", "temporary_memory.py",
      'validate_memory_privacy(\n                new_category, automatic=effective_source == "automatic"\n            )',
      "pass  # deliberate update-category privacy bypass",
      "test_temporary_memory_lifecycle.py", HA + "test_memory_provider_wire_e2e.py"),
    C("usage-timer-leak", "lifecycle", "usage.py",
      'self._cancel_retention()\n            self._cancel_retention = None',
      'pass  # deliberately preserve registered timer\n            self._cancel_retention = None',
      "test_idle_retention_remediation.py", HA + "test_usage_accounting_failure_recovery.py"),
    C("usage-request-retention-default", "persistence", "usage.py",
      'request_cutoff = now - timedelta(days=max(0, self.request_retention_days))\\n            run_cutoff',
      'request_cutoff = now - timedelta(days=30)\\n            run_cutoff',
      "test_idle_retention_remediation.py", HA + "test_usage_period_statistics.py"),
    C("guest-area-allowlist-ignored", "privacy", "guest_mode.py",
      'or bool(entity_areas & areas)',
      'or False',
      "test_privacy_ownership_remediation.py", HA + "test_generated_execution_privacy_workflows.py"),
    C("guest-entity-policy-bypass", "privacy", "guest_mode.py",
      'if not policy.guest_active:\n        return True',
      'if True:\n        return True',
      "test_privacy_ownership_remediation.py", HA + "test_generated_execution_privacy_workflows.py"),
    C("strict-schema-root-bypass", "provider", "entity.py",
      'if root and (schema.get("type") != "object" or "anyOf" in schema):',
      'if False:',
      "test_ai_task_composition_remediation.py", HA + "test_ai_task_provider_wire.py"),
    C("strict-schema-allof-bypass", "provider", "entity.py",
      'if "allOf" in schema:',
      'if False:',
      "test_ai_task_composition_remediation.py", HA + "test_ai_task_provider_wire.py"),
    C("strict-schema-freeform-bypass", "provider", "entity.py",
      'if schema.get("additionalProperties", False) is not False:',
      'if False:',
      "test_ai_task_composition_remediation.py", HA + "test_ai_task_provider_wire.py"),
    C("backup-expiry-callback-bypass", "lifecycle", "backup_transfer.py",
      'async def expire(_now: Any) -> None:\n        hass.data.pop(_EXPIRY_CANCEL_KEY, None)\n        await _async_cleanup_expired(hass)',
      'async def expire(_now: Any) -> None:\n        hass.data.pop(_EXPIRY_CANCEL_KEY, None)\n        pass  # missed expiry',
      "test_backup_transfer_protocol.py", HA + "test_native_ha_backup_restore.py"),
    C("backup-agent-deletion-bypass", "lifecycle", "backup_transfer.py",
      'if (session.entry_id, session.subentry_id) == (entry_id, subentry_id):',
      'if False:',
      "test_backup_transfer_protocol.py", HA + "test_native_ha_backup_restore.py"),
    C("rest-error-url-leak", "privacy", "functions/web.py",
      'f"REST request failed: {type(error).__name__}"',
      'f"REST request failed: {type(error).__name__}: {error}"',
      "test_provider_transport_diagnostics_privacy.py", HA + "test_provider_boundary_contracts.py"),
    C("indirect-targets-omitted", "authorization", "ha_actions.py",
      "return set(referenced.referenced | referenced.indirectly_referenced)",
      "return set(referenced.referenced)",
      "test_native_service_schema_rejection.py", HA + "test_generated_execution_privacy_workflows.py"),
    C("request-rule-condition-bypass", "rules", "request_rules.py",
      "if outcome is False:\n                    return False",
      "if False:\n                    return False",
      "test_request_rules_script_semantics.py", HA + "test_cross_feature_acceptance.py"),
    C("ai-task-schema-validation-bypass", "provider", "entity.py",
      '_adjust_schema(structured_schema, root=True)',
      'pass  # deliberately skip strict-schema preflight',
      "test_ai_task_composition_remediation.py", HA + "test_ai_task_provider_wire.py"),
)

assert len(EXPLORATORY) == 21
assert len({c.name for c in EXPLORATORY}) == 21
