#!/usr/bin/env bash
set -euo pipefail
set -o pipefail

TEST_PATHS=(
  tests_real_ha/test_acceptance_lifecycle.py
  tests_real_ha/test_ai_task_lifecycle.py
  tests_real_ha/test_ai_task_runtime.py
  tests_real_ha/test_assist_origin_context.py
  tests_real_ha/test_assist_streaming_speech_processing.py
  tests_real_ha/test_assist_voice_identity_precedence.py
  tests_real_ha/test_browser_backend_acceptance.py
  tests_real_ha/test_config_entry_disable_enable.py
  tests_real_ha/test_config_flow.py
  tests_real_ha/test_config_flow_edges.py
  tests_real_ha/test_config_flow_lifecycle_contracts.py
  tests_real_ha/test_entity_registry_customization.py
  tests_real_ha/test_entity_registry_disabled.py
  tests_real_ha/test_exposed_attribute_catalog_acceptance.py
  tests_real_ha/test_frontend_asset_registration.py
  tests_real_ha/test_ha_llm_tool_acceptance.py
  tests_real_ha/test_intercom_voice_acceptance.py
  tests_real_ha/test_management_backend_acceptance.py
  tests_real_ha/test_native_indirect_mixed_exposure.py
  tests_real_ha/test_native_indirect_target_resolution.py
  tests_real_ha/test_native_multi_indirect_target_union.py
  tests_real_ha/test_native_registry_state_disagreement.py
  tests_real_ha/test_native_service_disappearance.py
  tests_real_ha/test_native_service_schema_rejection.py
  tests_real_ha/test_native_target_disappearance.py
  tests_real_ha/test_native_unavailable_target.py
  tests_real_ha/test_partial_platform_setup_retry.py
  tests_real_ha/test_runtime_reauthentication.py
  tests_real_ha/test_security_routing_boundaries.py
  tests_real_ha/test_service_registry_acceptance.py
  tests_real_ha/test_user_permission_acceptance.py
)

pytest "${TEST_PATHS[@]}"   -v   --asyncio-mode=auto   --timeout=60   --durations=30   --junitxml=real-ha-junit.xml   2>&1 | tee real-ha-pytest.log
