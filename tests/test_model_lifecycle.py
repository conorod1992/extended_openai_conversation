"""Tests for model deprecation and retirement lifecycle handling."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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


@pytest.mark.parametrize(
    ("configured", "native"),
    [
        ({}, False),
        ({"base_url": ""}, False),
        ({"base_url": "https://api.openai.com/v1"}, True),
    ],
)
def test_model_lifecycle_resolves_implicit_endpoint_without_a_client(
    monkeypatch, configured, native
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://compatible.example/v1")
    subentry = SimpleNamespace(data={"chat_model": "gpt-5.1"})
    lifecycle = model_lifecycle.configured_lifecycle(
        subentry, today=date(2027, 4, 2), entry_data=configured,
    )
    assert lifecycle["shutdown_reached"] is native


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
        "The model gpt-5.1 does not exist PRIVATE-PROVIDER-CANARY",
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
    assert "PRIVATE-PROVIDER-CANARY" not in str(logger.error.call_args)
    assert "model_unavailable" in str(logger.error.call_args)
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

def test_independent_endpoints_clear_persisted_openai_retirement_repairs(monkeypatch):
    hass = _hass()
    subentry = SimpleNamespace(subentry_id="agent", subentry_type="conversation", title="Kitchen",
                               data={"chat_model": "gpt-5.1"})
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry}, data={})
    issue_id = model_lifecycle._issue_id("entry", "agent")
    issue = SimpleNamespace(data={"model": "gpt-5.1"}, active=True)
    registry = Mock()
    registry.async_get_issue.return_value = issue
    monkeypatch.setattr(model_lifecycle.ir, "async_get", lambda _hass: registry)
    delete = Mock()
    create = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_delete_issue", delete)
    monkeypatch.setattr(model_lifecycle.ir, "async_create_issue", create)
    snapshot = model_lifecycle.lifecycle_snapshot
    monkeypatch.setattr(model_lifecycle, "lifecycle_snapshot", lambda model, **_kwargs: snapshot(model, today=date(2027, 4, 2)))
    error = ProviderStreamError("missing", code="model_not_found", status_code=404)

    for independent in (
        {"api_provider": "openai", "base_url": "https://compatible.example/v1"},
        {"api_provider": "azure"},
        {"base_url": "https://kitchen.openai.azure.com"},
    ):
        entry.data = {"base_url": "https://api.openai.com/v1/"}
        model_lifecycle.sync_entry_model_lifecycle(hass, entry)
        assert model_lifecycle.confirmed_retirement_failure(hass, "entry", "agent", "gpt-5.1")
        delete.assert_not_called()
        entry.data = independent
        assert model_lifecycle.configured_lifecycle(subentry, entry_data=independent)["shutdown_reached"] is False
        assert model_lifecycle.record_retirement_failure(
            hass, entry_id="entry", subentry_id="agent", title="Kitchen",
            model="gpt-5.1", error=error, entry_data=independent,
        ) is None
        create.assert_not_called()
        hass.data.pop(model_lifecycle._DATA_FAILURES)
        model_lifecycle.sync_entry_model_lifecycle(hass, entry)
        delete.assert_called_once_with(hass, model_lifecycle.DOMAIN, issue_id)
        assert not model_lifecycle.confirmed_retirement_failure(hass, "entry", "agent", "gpt-5.1")
        delete.reset_mock()
