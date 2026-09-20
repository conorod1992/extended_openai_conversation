#!/usr/bin/env bash
set -euo pipefail

TEST_PATHS=(
  tests/test_ai_task.py
  tests/test_ai_task_migration.py
  tests/test_ai_task_model_options.py
  tests/test_ai_task_privacy.py
  tests/test_ai_task_web_search.py
  tests/test_broadcast_permissions.py
  tests/test_config_flow_coverage.py
  tests/test_configuration_lifecycle_hardening.py
  tests/test_conversation_entry_ownership.py
  tests/test_conversation_lifecycle.py
  tests/test_conversation_runtime_coverage.py
  tests/test_conversation_security_boundaries.py
  tests/test_entity_attachments.py
  tests/test_entity_content_conversion.py
  tests/test_entity_response_serialization.py
  tests/test_entity_schema_helpers.py
  tests/test_exposed_attributes.py
  tests/test_exposed_entities.py
  tests/test_exposed_entity_attributes.py
  tests/test_frontend_asset_registration.py
  tests/test_ha_actions.py
  tests/test_ha_actions_coverage.py
  tests/test_ha_llm_management.py
  tests/test_ha_llm_tools.py
  tests/test_ha_llm_tools_coverage.py
  tests/test_ha_permissions.py
  tests/test_ha_tool_result_compat.py
  tests/test_identity.py
  tests/test_intercom.py
  tests/test_intercom_runtime_contracts.py
  tests/test_native_action_execution_bounds.py
  tests/test_native_function_execution_budget.py
  tests/test_native_function_schema_contracts.py
  tests/test_process_service.py
  tests/test_reauthentication.py
  tests/test_scope_resolution.py
  tests/test_service_handlers.py
  tests/test_setup_cleanup_ownership.py
  tests/test_voice_identity_runtime.py
)

pytest "${TEST_PATHS[@]}"   -v   --asyncio-mode=auto   --timeout=30
