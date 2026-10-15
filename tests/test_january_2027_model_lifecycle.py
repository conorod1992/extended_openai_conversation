"""Keep January retirements on the existing, explicit model lifecycle path."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_lifecycle,
    runtime_failure_hardening,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
    merge_agent_config,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)

RETIRING_MODELS = (
    "gpt-4o",
    "gpt-4o-2024-08-06",
    "gpt-4o-2024-11-20",
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4.1",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini",
    "gpt-4.1-mini-2025-04-14",
)


@pytest.mark.parametrize("model", RETIRING_MODELS)
def test_january_shutdown_boundary_uses_exact_model_dates(model) -> None:
    before = model_lifecycle.lifecycle_snapshot(model, today=date(2027, 1, 13))
    shutdown = model_lifecycle.lifecycle_snapshot(model, today=date(2027, 1, 14))

    assert before["model"] == shutdown["model"] == model
    assert before["status"] == shutdown["status"] == "deprecated"
    assert before["deprecated_at"] == "2026-10-14"
    assert before["shutdown_at"] == "2027-01-14"
    assert before["shutdown_reached"] is False
    assert shutdown["shutdown_reached"] is True


@pytest.mark.parametrize("model", RETIRING_MODELS)
@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_deprecated_configuration_round_trip_preserves_model_and_request(
    model, api_mode
) -> None:
    saved = {
        "chat_model": model,
        "api_mode": api_mode,
        "functions": [],
        "max_tokens": 500,
        "temperature": 0.4,
    }
    original = deepcopy(saved)
    normalized = normalize_agent_config(saved)
    displayed = agent_config_snapshot(normalized)
    updated = merge_agent_config(displayed, {"max_tokens": 600})
    request = build_provider_request_snapshot(updated, {}, tools_required=True)

    assert saved == original
    assert normalized["chat_model"] == displayed["chat_model"] == model
    assert updated["chat_model"] == request.api_kwargs["model"] == model
    assert request.api_mode == api_mode
    assert request.api_kwargs["temperature"] == 0.4
    assert "reasoning" not in request.api_kwargs
    assert "reasoning_effort" not in request.api_kwargs


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o-2024-11-20",
        "gpt-4o-mini-2024-07-18",
        "gpt-4.1-2025-04-14",
        "gpt-4.1-mini-2025-04-14",
    ],
)
def test_new_snapshot_metadata_preserves_existing_auto_api_choice(model) -> None:
    saved = {"chat_model": model, "api_mode": "auto", "functions": []}

    request = build_provider_request_snapshot(saved, {}, tools_required=False)

    # These IDs previously used the conservative custom-model Responses path.
    assert request.api_mode == "responses"
    assert request.api_kwargs["model"] == saved["chat_model"] == model
    assert saved["api_mode"] == "auto"


@pytest.mark.parametrize("model", RETIRING_MODELS)
@pytest.mark.parametrize(
    ("today", "expected_retirement"),
    [(date(2027, 1, 13), False), (date(2027, 1, 14), True)],
)
def test_provider_failure_after_shutdown_is_actionable_without_model_migration(
    monkeypatch, model, today, expected_retirement
) -> None:
    snapshot = model_lifecycle.lifecycle_snapshot
    monkeypatch.setattr(
        model_lifecycle,
        "lifecycle_snapshot",
        lambda value: snapshot(value, today=today),
    )
    create_issue = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_create_issue", create_issue)
    monkeypatch.setattr(runtime_failure_hardening, "request_reauthentication", Mock())
    monkeypatch.setattr(runtime_failure_hardening, "record_current_provider_failure", Mock())
    saved = {"chat_model": model, "api_mode": "responses"}
    entity = SimpleNamespace(
        hass=SimpleNamespace(data={}),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(
            data=saved, subentry_id="assistant", title="Kitchen Assistant"
        ),
        _fire_conversation_finished=Mock(),
    )
    error = ProviderStreamError(
        "The configured model does not exist", code="model_not_found", status_code=404
    )

    result = runtime_failure_hardening._conversation_error_result(
        entity,
        SimpleNamespace(language="en", conversation_id="conversation"),
        SimpleNamespace(),
        error,
        logger=Mock(),
    )

    assert saved == {"chat_model": model, "api_mode": "responses"}
    assert result.conversation_id == "conversation"
    assert model_lifecycle.confirmed_retirement_failure(
        entity.hass, "entry", "assistant", model
    ) is expected_retirement
    speech = result.response.speech["plain"]["speech"]
    if expected_retirement:
        assert f"{model} appears to have been retired" in speech
        assert "2027-01-14" in speech
        assert "Select a different model" in speech
        assert create_issue.call_args.kwargs["data"]["model"] == model
    else:
        assert "retired" not in speech
        create_issue.assert_not_called()


@pytest.mark.parametrize("subentry_type", ["conversation", "ai_task_data"])
def test_lifecycle_sync_warns_once_and_preserves_legacy_configuration(
    monkeypatch, caplog, subentry_type
) -> None:
    saved = {"chat_model": "gpt-4o-mini", "temperature": 0.4}
    entry = SimpleNamespace(
        entry_id="entry",
        subentries={
            "assistant": SimpleNamespace(
                subentry_id="assistant",
                subentry_type=subentry_type,
                title="Kitchen Assistant",
                data=saved,
            )
        },
    )
    hass = SimpleNamespace(data={})
    monkeypatch.setattr(
        model_lifecycle.ir,
        "async_get",
        lambda _hass: SimpleNamespace(async_get_issue=lambda *_args: None),
    )

    model_lifecycle.sync_entry_model_lifecycle(hass, entry)
    model_lifecycle.sync_entry_model_lifecycle(hass, entry)

    assert saved == {"chat_model": "gpt-4o-mini", "temperature": 0.4}
    assert caplog.text.count("uses deprecated model gpt-4o-mini") == 1
    assert "scheduled API shutdown 2027-01-14" in caplog.text


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4.1-nano",
        "gpt-4o-audio-preview",
        "gpt-4o-2024-05-13",
        "ft:gpt-4o-mini-2024-07-18:example:custom:abc123",
    ],
)
def test_similar_or_custom_ids_do_not_inherit_january_retirement(model) -> None:
    lifecycle = model_lifecycle.lifecycle_snapshot(model, today=date(2027, 1, 14))

    assert lifecycle["shutdown_at"] != "2027-01-14"
    assert lifecycle["shutdown_reached"] is False


def test_default_model_remains_current_and_is_not_a_retiring_fallback() -> None:
    assert DEFAULT_CHAT_MODEL == agent_config_defaults()["chat_model"] == "gpt-5-mini"
    assert DEFAULT_CHAT_MODEL not in RETIRING_MODELS
    assert model_lifecycle.lifecycle_snapshot(DEFAULT_CHAT_MODEL)["status"] == "current"
