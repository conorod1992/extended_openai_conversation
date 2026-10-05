"""Turn deterministic enhanced-test traces into a useful GitHub Step Summary."""

from collections import Counter
import json
import os
from pathlib import Path
import sys

from enhanced_evidence import envelope, safe, write_json

COUNT_METRICS = {
    "generated_editor_programmes",
    "multilingual_public_journeys",
    "generated_editor_restarts",
    "unicode_character_boundary_cases",
    "unicode_byte_boundary_cases",
    "application_privacy_journeys",
    "raw_application_destination_checks",
    "authorized_export_privacy_cases",
    "downstream_websocket_cases",
    "downstream_audio_cases",
    "downstream_bounded_samples",
    "shared_satellite_ownership_cases",
    "shared_runtime_ownership_cases",
    "nonzero_backoff_cancel_cases",
    "active_nonterminal_stream_cases",
    "active_contention_journeys",
    "active_native_workloads",
    "active_workload_completions",
    "contention_foreground_requests",
    "contention_websocket_saves",
    "provider_isolation_live_saves",
    "pre_gc_resource_samples",
    "genuine_management_websocket_commands",
    "populated_ageing_features",
    "generated_public_schedule_cases",
    "integrated_calendar_journeys",
    "delayed_worker_settlement_checks",
    "shared_backend_business_cases",
    "shared_durable_schedules",
    "consumer_first_recoveries",
    "populated_feature_journeys",
    "semantic_oracle_rejections",
    "filesystem_recovery_cases",
    "filesystem_recovery_kills",
    "provider_endpoint_wire_cases",
    "native_edit_settlement_cases",
    "native_file_settlement_cases",
    "process_fresh_boots",
    "native_restore_faults",
    "restore_pending_cases",
    "restore_manager_settlement_cases",
    "restore_journal_fault_cases",
    "restore_recovery_cold_cases",
    "rejected_recovery_operations",
    "rule_publication_cases",
    "guest_ambiguous_ack_cases",
    "skill_download_cancellation_cases",
    "skill_download_unowned_cases",
    "interrupted_active_speech_listener_cancel",
    "interrupted_active_speech_listener_unload",
    "explicit_stream_close_cancel",
    "explicit_stream_close_unload",
    "explicit_stream_close_cases",
    "natural_stream_exhaustion_close_negatives",
    "clean_recovery_requests",
    "interleaved_exact_usage_rounds",
    "interleaved_exact_usage_negative_controls",
    "interleaved_exact_usage_api_modes",
    "exploration_corpus_cases_replayed",
    "retention_restore_ownership_cases",
    "coherent_setup_restore_exports",
    "quiet_pending_manual_override_checks",
    "archive_first_intent_failure_cases",
    "archive_first_intent_disk_commits",
    "archive_intent_reload_checks",
    "pending_publication_failure_cases",
    "pending_publication_retry_checks",
    "pending_publication_actual_tool_calls",
    "quiet_repeated_hour_cases",
    "quiet_monotonic_utc_checks",
    "quiet_exact_transition_checks",
    "quiet_post_change_failure_cases",
    "quiet_durable_baseline_checks",
    "quiet_manual_failure_checks",
    "quiet_registry_rename_cases",
    "quiet_renamed_transition_checks",
    "quiet_registry_restart_checks",
    "quiet_storage_failure_cases",
    "quiet_storage_retry_checks",
    "quiet_storage_restoration_checks",
    "ai_task_image_continuations",
    "ai_task_image_isolation_checks",
    "catalog_ai_task_reset_rejections",
    "catalog_ai_task_resets",
    "catalog_ai_task_wire_checks",
    "native_editor_ownership_cases",
    "native_registry_recovery_cases",
    "native_registry_fetch_recoveries",
    "native_ownership_reload_checks",
    "native_registry_private_probes",
    "concurrent_audio_journeys",
    "audio_interleavings",
    "targeted_tts_failures",
    "native_audio_recoveries",
    "production_backup_browser_journeys",
    "production_backup_seed_bytes",
    "native_control_endurance_journeys",
    "genuine_locale_timezone_journeys",
    "intercom_timing_cases",
    "intercom_idle_flaps",
    "actual_expiry_callbacks",
    "delayed_playback_acknowledgements",
    "intercom_timing_recoveries",
    "native_lifetime_journeys",
    "native_composite_rejections",
    "native_composite_recoveries",
    "native_abandoned_uploads",
    "native_upload_bytes",
    "native_transfer_reclaims",
    "native_retention_windows",
    "native_keyboard_commits",
    "native_assistant_states",
    "native_held_completions",
    "native_revision_conflicts",
    "native_semantic_scans",
    "native_error_semantic_scans",
    "lifetime_elapsed_seconds",
    "lifetime_idle_seconds",
    "lifetime_retention_callbacks",
    "lifetime_expired_transfer_reclaims",
    "lifetime_reloads",
    "lifetime_final_healthy_requests",
    "artifact_privacy_probes",
    "assist_parent_cancel_cases",
    "assist_sibling_failure_cases",
    "bash_pipe_settlements",
    "bash_pipe_tree_cases",
    "budget_atomic_cases",
    "budget_loading_replay_cases",
    "budget_serial_prefix_cases",
    "cancelled_maintenance_waiters",
    "completed_replay_rejections",
    "continuity_owner_transitions",
    "continuity_wire_checks",
    "continuity_serialized_overlaps",
    "composite_ingestion_cases",
    "composite_ingestion_rejections",
    "compressed_remote_cases",
    "debug_export_privacy_cases",
    "delayed_backlog_restarts",
    "retained_manager_recoveries",
    "compound_optional_manager_faults",
    "rest_scoped_requests",
    "rest_overlapping_requests",
    "rest_transport_failure_cases",
    "rest_transport_recoveries",
    "rest_blocked_dependent_actions",
    "rest_received_response_controls",
    "late_external_edit_conflicts",
    "late_external_edit_recoveries",
    "quiet_saved_control_manual_changes",
    "quiet_prepared_recovery_cases",
    "quiet_independent_goal_preservations",
    "quiet_process_application_recoveries",
    "quiet_process_indeterminate_controls",
    "quiet_process_healthy_periods",
    "quiet_service_acknowledgement_recoveries",
    "delayed_same_manager_recoveries",
    "delayed_compound_storage_faults",
    "delayed_recovered_service_effects",
    "delayed_healthy_retry_effects",
    "delayed_indeterminate_replay_rejections",
    "delayed_foreground_during_backlog",
    "delayed_partial_termination_cases",
    "delayed_retryable_pre_dispatch_failures",
    "distinct_matcher_pressure_cases",
    "mixed_maintenance_contention_cases",
    "mixed_usage_summary_cases",
    "permission_cache_churn_cases",
    "permission_group_updates",
    "populated_api_transition_cases",
    "regex_worker_settlements",
    "schema_pattern_boundary_cases",
    "schema_contract_rejections",
    "normalized_schema_dispatch_cases",
    "normalized_schema_rejections",
    "normalized_schema_healthy_dispatches",
    "native_history_worker_settlements",
    "native_history_cancelled_recoveries",
    "native_automation_layout_cases",
    "native_automation_load_rejections",
    "native_disabled_automation_loads",
    "schema_healthy_tool_recoveries",
    "schema_bounded_regex_exchanges",
    "restricted_context_probes",
    "shared_http_pool_pressure_cases",
    "shared_http_waiter_recoveries",
    "storage_failure_recoveries",
    "summary_lifecycle_cases",
    "summary_replacement_recoveries",
    "sqlite_lock_waits",
    "sqlite_worker_settlements",
    "sqlite_healthy_recoveries",
    "embedding_association_cases",
    "embedding_cache_rechecks",
    "embedding_malformed_responses",
    "embedding_live_configuration_races",
    "embedding_reload_races",
    "embedding_healthy_recoveries",
    "assist_safe_overlap",
    "assist_mixed_serial",
    "remote_resource_recovery_cases",
    "composite_rejected_trees",
    "composite_cancelled_exchanges",
    "concurrent_summary_cases",
    "delayed_backlog_calls",
    "delayed_backlog_executions",
    "audio_deliveries",
    "audio_playback_acknowledgements",
    "process_soak_windows",
    "uninterrupted_lifetime_windows",
    "uninterrupted_lifetime_reloads",
    "uninterrupted_lifetime_final_healthy_requests",
    "large_responses_wire_journeys",
    "large_responses_memory_records",
    "large_responses_knowledge_sources",
    "large_responses_function_tools",
    "resource_fairness_cases",
    "resource_fairness_lightweight_requests",
    "tls_verified_requests",
    "transport_tool_recovery_cases",
    "conversation_turns",
    "lifecycle_cycles",
    "public_conversation_turns",
    "ai_task_turns",
    "ai_task_concurrent",
    "ai_task_agents",
    "ai_task_provider_failures",
    "voice_household_turns",
    "voice_household_users",
    "voice_household_satellites",
    "voice_mapping_changes",
    "voice_user_deletions",
    "voice_private_context_probes",
    "voice_temporary_context_probes",
    "voice_guest_context_probes",
    "intercom_broadcasts",
    "intercom_satellites",
    "intercom_deliveries",
    "intercom_failures",
    "intercom_expired",
    "archive_turns_written",
    "archive_scopes",
    "archive_privacy_transitions",
    "archive_searches",
    "local_intent_turns",
    "local_intent_provider_fallbacks",
    "local_intent_policy_reloads",
    "skill_lifecycle_operations",
    "skill_publishes",
    "skill_removals",
    "skill_scans",
    "skill_blocked_mutations",
    "public_turns",
    "provider_requests",
    "embedding_provider_requests",
    "actual_tool_executions",
    "actual_function_executions",
    "local_function_executions",
    "native_function_executions",
    "template_function_executions",
    "script_function_executions",
    "rest_function_executions",
    "scrape_function_executions",
    "composite_function_executions",
    "sqlite_function_executions",
    "bash_function_executions",
    "read_file_function_executions",
    "write_file_function_executions",
    "edit_file_function_executions",
    "provider_wire_function_errors",
    "delayed_tool_mutation_cases",
    "delayed_tool_due_executions",
    "ha_service_calls",
    "guest_end_to_end_combinations",
    "private_context_probes",
    "rollback_phases",
    "backup_chunks_transferred",
    "transfer_sessions",
    "concurrent_import_sessions",
    "transfer_previews",
    "stale_apply_rejections",
    "expired_import_sessions",
    "cancelled_import_sessions",
    "historical_fixtures",
    "browser_creates",
    "browser_edits",
    "browser_deletes",
    "multi_tab_conflicts",
    "stale_responses",
    "reconnect_cycles",
    "reconnect_mutation_cycles",
    "accessibility_layout_pages",
    "quiet_hours_transitions",
    "quiet_ownership_cases",
    "quiet_heterogeneous_devices",
    "quiet_active_policy_mutations",
    "quiet_transient_service_failures",
    "quiet_time_boundary_cases",
    "quiet_dst_cases",
    "chaos_operations",
    "process_terminations",
    "setup_flows",
}


def layer_for(test: str, operations: list[dict]) -> str:
    """Classify evidence by its deepest exercised boundary, not by test count."""
    explicit = next(
        (item.get("layer") for item in operations if item.get("layer")), None
    )
    if explicit:
        return str(explicit)
    if "browser" in test or test.endswith(".stress.mjs"):
        return "browser"
    if "function_groups_state_machine" in test or "request_rules_matrix" in test:
        return "model-level"
    return "real-ha"


def main() -> None:
    folder = Path(sys.argv[1])
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    campaign = os.environ.get("STRESS_CAMPAIGN", "unknown")
    seed = os.environ.get("STRESS_SEED", "unknown")
    intensity = os.environ.get("STRESS_INTENSITY", "normal")
    status = os.environ.get("ENHANCED_JOB_STATUS", "unknown")
    metadata = envelope(status=status)
    lines = [
        f"### Enhanced acceptance: {campaign}",
        "",
        f"SHA: `{metadata['eoai_sha']}` · Seed: `{seed}` · Intensity: `{intensity}` · Status: **{status}**",
        f"HA: `{metadata['ha_version'] or 'not applicable'}` · Python: `{metadata['python_version']}`",
        "",
    ]
    execution_cases = []
    execution_runs = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("execution_schema") == "eoai-test-execution/v1":
            execution_id = data.get("execution_id")
            execution_runs.append(
                {
                    key: data.get(key)
                    for key in (
                        "execution_id",
                        "eoai_sha",
                        "runner",
                        "exit_status",
                        "status",
                        "environment",
                        "environment_fingerprint",
                    )
                }
            )
            execution_cases.extend(
                {**case, "execution_id": execution_id} for case in data["cases"]
            )
    totals: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    if not files:
        lines += [
            "Selected Real HA tests report their assertions in the pytest log; no enhanced operation trace was produced.",
            "",
        ]
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("test") and not path.stem.startswith("browser-"):
            continue
        operations = safe(data.get("operations", []))
        outcome = data.get("outcome") or next(
            (
                case["outcome"]
                for case in execution_cases
                if case["nodeid"] == data.get("test")
            ),
            "unreported",
        )
        outcomes[outcome] += 1
        counts = Counter(item.get("operation", "unknown") for item in operations)
        lines += [
            f"**{data.get('test', path.stem)}**",
            "",
            f"Outcome: **{outcome}** · {'Exercised' if outcome == 'passed' else 'Attempted'} layer: **{'browser' if path.stem.startswith('browser-') else layer_for(data.get('test', path.stem), operations)}** · Trace events: {len(operations)}",
            "",
        ]
        if counts:
            lines += ["| Operation | Count |", "| --- | ---: |"]
            lines += [f"| {name} | {count} |" for name, count in sorted(counts.items())]
            lines.append("")
        for item in operations:
            if item.get("operation") == "summary" and outcome == "passed":
                for key in COUNT_METRICS:
                    value = item.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        totals[key] += value
                details = ", ".join(
                    f"{key}={safe(value, key)}"
                    for key, value in item.items()
                    if key not in {"operation", "number"}
                )
                lines += [f"Measured: {details}", ""]
        if "maxNodes" in data:
            totals["browser_creates"] += int(data.get("creates", 0))
            totals["browser_edits"] += int(data.get("edits", 0))
            totals["browser_deletes"] += int(data.get("deletes", 0))
            lines += [
                f"Browser transitions: {data['count']}; creates: {data.get('creates', 0)}; edits: {data.get('edits', 0)}; deletes: {data.get('deletes', 0)}; maximum observed panel DOM nodes: {data['maxNodes']}",
                "",
            ]
        if data.get("retention"):
            totals["browser_retention_windows"] += len(data["retention"])
            lines += [
                f"Chromium post-GC retention windows: {len(data['retention'])}; findings: {data.get('retentionFindings', [])}",
                "",
            ]
        if "cycles" in data:
            totals["reconnect_cycles"] += int(data["cycles"])
            totals["reconnect_mutation_cycles"] += int(data.get("mutationCycles", 0))
            lines += [
                f"Reconnect cycles: {data['cycles']}; mutation cycles: {data.get('mutationCycles', 0)}; baseline backend calls per forced read: {data.get('baselineCalls')}",
                "",
            ]
        if "staleResponses" in data:
            totals["stale_responses"] += int(data["staleResponses"])
            lines += [
                f"Injected stale responses by surface: {data.get('staleBySurface', {})}",
                "",
            ]
        if "accessibilityLayoutPages" in data:
            totals["accessibility_layout_pages"] += int(
                data["accessibilityLayoutPages"]
            )
            lines += [
                f"Accessibility/layout page-width combinations checked: {data['accessibilityLayoutPages']}",
                "",
            ]
    if totals:
        lines += ["**Measured totals**", "", "| Metric | Count |", "| --- | ---: |"]
        lines += [f"| {key} | {value} |" for key, value in sorted(totals.items())]
        lines.append("")
    if outcomes:
        lines += ["**Trace outcomes**", "", "| Outcome | Count |", "| --- | ---: |"]
        lines += [f"| {name} | {value} |" for name, value in sorted(outcomes.items())]
        lines.append("")
    summary = "\n".join(lines)
    write_json(
        folder / "certification.json",
        {
            **metadata,
            "tests_traced": sum(
                1 for path in files if path.name != "certification.json"
            ),
            "measured_totals": dict(totals),
            "execution_cases": execution_cases,
            "execution_runs": execution_runs,
            "trace_outcomes": dict(outcomes),
            "artifact_files": [
                path.name for path in files if path.name != "certification.json"
            ],
        },
    )
    print(summary)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary + "\n")


if __name__ == "__main__":
    main()
