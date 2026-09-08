"""Regression coverage for structured and legacy transfer redaction markers."""

from custom_components.extended_openai_conversation_responses import transfer
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL,
)


def test_redaction_placeholder_detection_accepts_structured_and_legacy_forms() -> None:
    assert transfer._is_redacted_placeholder(REDACTED_SECRET_SENTINEL)
    assert transfer._is_redacted_placeholder("[redacted]")
    assert not transfer._is_redacted_placeholder({"ordinary": "mapping"})
    assert not transfer._is_redacted_placeholder("ordinary value")
