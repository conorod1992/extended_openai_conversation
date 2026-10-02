"""Tests for model deprecation and retirement lifecycle handling."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.extended_openai_conversation_responses import model_lifecycle
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)


def _hass():
    return SimpleNamespace(data={})


def test_structured_deprecation_dates_drive_shutdown_phase() -> None:
    before = model_lifecycle.lifecycle_snapshot("gpt-5.1", today=date(2027, 3, 31))
    after = model_lifecycle.lifecycle_snapshot("gpt-5.1", today=date(2027, 4, 1))

    assert before["status"] == "deprecated"
    assert before["deprecated_at"] == "2026-10-01"
    assert before["shutdown_at"] == "2027-04-01"
    assert before["shutdown_reached"] is False
    assert after["shutdown_reached"] is True


def test_model_unavailable_error_classification_is_narrow() -> None:
    assert model_lifecycle.retirement_provider_error(
        ProviderStreamError("model missing", code="model_not_found", status_code=404)
    )
    assert not model_lifecycle.retirement_provider_error(
        ProviderStreamError(
            "quota exceeded", code="rate_limit_exceeded", status_code=429
        )
    )
    assert not model_lifecycle.retirement_provider_error(
        ProviderStreamError("not found", code="resource_not_found", status_code=404)
    )


def test_retirement_failure_creates_repair_and_deduplicates_log(monkeypatch) -> None:
    hass = _hass()
    create = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_create_issue", create)
    monkeypatch.setattr(
        model_lifecycle,
        "lifecycle_snapshot",
        lambda _model: {
            "model": "gpt-5.1",
            "status": "deprecated",
            "deprecated_at": "2026-10-01",
            "shutdown_at": "2027-04-01",
            "lifecycle_note": "retired",
            "shutdown_reached": True,
        },
    )
    logger = Mock()
    error = ProviderStreamError(
        "The model gpt-5.1 does not exist",
        code="model_not_found",
        status_code=404,
    )

    first = model_lifecycle.record_retirement_failure(
        hass,
        entry_id="entry",
        subentry_id="agent",
        title="Kitchen Assistant",
        model="gpt-5.1",
        error=error,
        logger=logger,
    )
    second = model_lifecycle.record_retirement_failure(
        hass,
        entry_id="entry",
        subentry_id="agent",
        title="Kitchen Assistant",
        model="gpt-5.1",
        error=error,
        logger=logger,
    )

    assert "appears to have been retired" in first
    assert second == first
    assert create.call_count == 2
    logger.error.assert_called_once()
    assert model_lifecycle.confirmed_retirement_failure(
        hass, "entry", "agent", "gpt-5.1"
    )


def test_retirement_failure_is_not_inferred_before_shutdown(monkeypatch) -> None:
    hass = _hass()
    create = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_create_issue", create)
    monkeypatch.setattr(
        model_lifecycle,
        "lifecycle_snapshot",
        lambda _model: {
            "model": "gpt-5.1",
            "status": "deprecated",
            "shutdown_at": "2027-04-01",
            "shutdown_reached": False,
        },
    )
    error = ProviderStreamError("missing", code="model_not_found", status_code=404)

    assert (
        model_lifecycle.record_retirement_failure(
            hass,
            entry_id="entry",
            subentry_id="agent",
            title="Assistant",
            model="gpt-5.1",
            error=error,
        )
        is None
    )
    create.assert_not_called()


def test_clearing_failure_resolves_repair(monkeypatch) -> None:
    hass = _hass()
    hass.data[model_lifecycle._DATA_FAILURES] = {
        ("entry", "agent"): {"model": "gpt-5.1", "shutdown_at": "2027-04-01"}
    }
    delete = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_delete_issue", delete)

    model_lifecycle.clear_retirement_failure(
        hass, entry_id="entry", subentry_id="agent", model="gpt-5.1"
    )

    assert hass.data[model_lifecycle._DATA_FAILURES] == {}
    delete.assert_called_once()
