"""Contracts for provider transport and diagnostics privacy acceptance."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "tests_real_ha" / "test_provider_transport_diagnostics_privacy.py"


def test_provider_transport_privacy_acceptance_keeps_all_reviewed_boundaries() -> None:
    """Protect the four reviewed provider/diagnostics obligations from being dropped."""
    source = ACCEPTANCE.read_text(encoding="utf-8")
    required = (
        "test_http_200_invalid_provider_body_fails_then_next_turn_recovers",
        "CAPTIVE_PORTAL_ASSISTANT_MARKER",
        "test_cross_origin_provider_redirect_never_forwards_credentials",
        "authorization",
        "api-key",
        "301, 302, 307, 308",
        "test_diagnostics_during_active_tool_continuation_are_safe_and_non_intrusive",
        "PRIVATE_ACTIVE_REQUEST_DIAGNOSTIC_MARKER",
        "test_diagnostics_after_native_credential_rotation_redact_old_and_new_secrets",
        "SOURCE_REAUTH",
        "async_get_config_entry_diagnostics",
    )
    for marker in required:
        assert marker in source, marker
