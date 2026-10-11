"""Provider-boundary regressions for audit findings 1, 5, 6, 7 and 8."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.agent_test import (
    async_test_agent,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.provider_content import (
    answer_text,
)
from custom_components.extended_openai_conversation_responses.request import (
    auxiliary_output_token_limit,
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_saved_model_routes,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_agent_test import _objects
from tests.test_backup import _document
from tests.test_transfer import _entry_and_subentry


@pytest.mark.parametrize(
    "provider,model,underlying,expected",
    [
        (
            {"base_url": "https://api.mistral.ai/v1"},
            "mistral-small-latest",
            None,
            "max_tokens",
        ),
        ({"api_provider": "azure"}, "deployment", "gpt-4.1", "max_completion_tokens"),
        ({}, "gpt-4.1", None, "max_completion_tokens"),
    ],
)
def test_auxiliary_token_selection_matches_live_request(
    provider, model, underlying, expected
):
    options = {"chat_model": model, "api_mode": "chat_completions", "max_tokens": 16}
    if underlying:
        options["azure_model"] = underlying
    assert auxiliary_output_token_limit(
        model, "chat_completions", 16, options, provider
    ) == (expected, 16)
    live = build_provider_request_snapshot(options, provider, tools_required=False)
    assert live.api_kwargs[expected] == 16


async def test_custom_test_agent_sends_legacy_token_field():
    hass, entry, subentry, client, usage = _objects(
        {"chat_model": "mistral-small-latest", "max_function_calls_per_conversation": 0}
    )
    entry.data = {"base_url": "https://api.mistral.ai/v1"}
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.async_get_usage",
            AsyncMock(return_value=usage),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities",
            return_value=[],
        ),
    ):
        result = await async_test_agent(hass, entry, subentry)
    assert client.chat.completions.create.await_args is not None, result
    sent = client.chat.completions.create.await_args.kwargs
    assert sent["max_tokens"] == 16 and "max_completion_tokens" not in sent


async def test_custom_summary_uses_provider_aware_tokens():
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="summary"), finish_reason="stop"
                )
            ],
            usage=None,
        )
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        data={"base_url": "https://api.mistral.ai/v1"},
        runtime_data=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    entity.subentry = SimpleNamespace(data={})
    entity._usage = None
    assert (
        await entity._async_summarize_history(
            [conversation.UserContent(content="Earlier request")],
            "mistral-small-latest",
            "chat_completions",
        )
        == "summary"
    )
    assert create.await_args.kwargs["max_tokens"] == 256


@pytest.mark.parametrize(
    "content,expected",
    [
        ("Answer", "Answer"),
        (
            [
                {"type": "thinking", "thinking": "PRIVATE"},
                {"type": "text", "text": "Hello"},
                SimpleNamespace(type="text", text=" world"),
            ],
            "Hello world",
        ),
        (
            [
                {"type": "unknown", "text": "PRIVATE"},
                {"type": "output_text", "text": "Answer"},
            ],
            "Answer",
        ),
        ({"type": "text", "text": {"secret": "PRIVATE"}}, ""),
    ],
)
def test_answer_parts_never_stringify_reasoning(content, expected):
    assert answer_text(content) == expected


def test_azure_model_change_revalidates_api_pairing():
    current = {
        "chat_model": "deployment",
        "azure_model": "gpt-4.1",
        "api_mode": "responses",
        "functions": [],
    }
    with pytest.raises(AgentConfigError):
        management_ui._validated_model_request(
            {**current, "azure_model": "gpt-4"}, {"api_provider": "azure"}, current
        )


def test_retained_static_route_uses_proposed_api_mode():
    rule = {
        "id": "old-route",
        "enabled": True,
        "action_type": "model_routing",
        "action": {"model": "gpt-4", "reasoning_effort": ""},
    }
    with pytest.raises(HomeAssistantError, match="old-route"):
        validate_saved_model_routes(
            [rule],
            {"chat_model": "gpt-4.1", "api_mode": "responses", "functions": []},
            {},
        )
    validate_saved_model_routes(
        [{**rule, "enabled": False}], {"api_mode": "responses"}, {}
    )


async def test_restore_checks_destination_provider_before_any_write():
    current = backup.inspect_backup(_document(), "target-agent")
    current.config.update(chat_model="gpt-4.1", api_mode="responses", web_search=True)
    imported = transfer.PreparedTransfer(
        source_kind="portable_transfer",
        mode="setup",
        title="Imported",
        available_sections=frozenset({transfer.SECTION_CONFIGURATION}),
        created_at="2026-10-11T00:00:00+00:00",
        integration_version="5",
        config=dict(current.config),
    )
    entry, subentry = _entry_and_subentry()
    entry.data = {"api_provider": "azure"}
    with pytest.raises(backup.BackupError, match="destination provider"):
        await transfer.async_materialize_restore(
            SimpleNamespace(), entry, subentry, imported, current_snapshot=current
        )


@pytest.mark.parametrize(
    "model,provider,extra",
    [
        ("gpt-4", {}, {}),
        (
            "mistral-small-latest",
            {"base_url": "https://api.mistral.ai/v1"},
            {},
        ),
        (
            "deployment",
            {"api_provider": "azure"},
            {"azure_model": "gpt-4"},
        ),
        ("gpt-4.1", {}, {}),
    ],
)
async def test_image_auto_mode_uses_requested_model_capabilities(
    hass, monkeypatch, model, provider, extra
):
    from custom_components.extended_openai_conversation_responses import services
    from custom_components.extended_openai_conversation_responses.const import (
        DOMAIN,
        SERVICE_QUERY_IMAGE,
    )

    response = SimpleNamespace(model_dump=lambda: {}, status="completed")
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        ),
        responses=SimpleNamespace(create=AsyncMock(return_value=response)),
    )
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=DOMAIN, data=provider, runtime_data=client
    )
    monkeypatch.setattr(services, "_async_require_service_admin", AsyncMock())
    monkeypatch.setattr(
        services,
        "prepare_image_params",
        lambda *args: [{"url": "data:image/png;base64,AA=="}],
    )
    await services.async_setup_services(hass, {})
    handler = next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[1] == SERVICE_QUERY_IMAGE
    )
    from custom_components.extended_openai_conversation_responses.request import (
        request_model_capabilities,
    )
    from custom_components.extended_openai_conversation_responses.model_capabilities import (
        model_capability_snapshot,
    )

    with model_capability_snapshot(
        model, request_model_capabilities({"chat_model": model, **extra}, provider)
    ):
        expected = services.get_api_mode("auto", model)
    await handler(
        SimpleNamespace(
            data={
                "config_entry": "entry",
                "model": model,
                "api_mode": "auto",
                "max_tokens": 300,
                "prompt": "describe",
                "images": [],
                **extra,
            }
        )
    )
    if expected == "chat_completions":
        client.chat.completions.create.assert_awaited_once()
        client.responses.create.assert_not_called()
    else:
        client.responses.create.assert_awaited_once()
        client.chat.completions.create.assert_not_called()


def test_malformed_answer_part_type_is_ignored():
    assert (
        answer_text(
            [{"type": ["text"], "text": "PRIVATE"}, {"type": "text", "text": "Answer"}]
        )
        == "Answer"
    )


def test_dynamic_reasoning_does_not_hide_static_model_api_incompatibility():
    rule = {
        "action_type": "model_routing",
        "enabled": True,
        "name": "route",
        "action": {"model": "gpt-4", "reasoning_effort": "{effort}"},
    }
    with pytest.raises(HomeAssistantError):
        validate_saved_model_routes(
            [rule],
            {"chat_model": "gpt-4.1", "api_mode": "responses", "functions": []},
            {},
        )
