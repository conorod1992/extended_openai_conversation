"""Additional reachable maintenance and request-rule edge contracts."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_summary_performance as summaries,
    conversation as conv,
    function_dependency_integrity as integrity,
    model_catalog as catalog,
    model_catalog_manager as models,
    provider_errors,
    request_rules as rules_module,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.context_summary import (
    DeferredContextSummaryManager,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_model_catalog_refresh_failures import MemoryStore
from tests.test_quiet_hours_runtime import _public_manager
from tests.test_request_rules import local_rule, manager


@pytest.mark.parametrize("recovery", [False, True])
async def test_conversation_platform_setup_distinguishes_recovery_from_other_failures(
    hass, monkeypatch, recovery
):
    child = SimpleNamespace(subentry_type="conversation", subentry_id="agent")
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": child})
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    gate.recovery_required = recovery
    entity = Mock(side_effect=HomeAssistantError("setup failed"))
    monkeypatch.setattr(conv, "ExtendedOpenAIAgentEntity", entity)
    add = Mock()
    if recovery:
        await conv.async_setup_entry(hass, entry, add)
        entity.assert_not_called()
    else:
        with pytest.raises(HomeAssistantError, match="setup failed"):
            await conv.async_setup_entry(hass, entry, add)
    add.assert_not_called()


async def test_summary_scheduling_declines_short_history_without_creating_task():
    entity = SimpleNamespace(
        subentry=SimpleNamespace(
            data={
                summaries.CONF_CONTEXT_TRUNCATE_STRATEGY: summaries.CONTEXT_TRUNCATE_SUMMARIZE
            }
        ),
        entry=SimpleNamespace(async_create_task=Mock()),
        hass=object(),
    )
    log = SimpleNamespace(
        conversation_id="conversation",
        continue_conversation=False,
        content=[
            conversation.SystemContent(content="system"),
            conversation.UserContent(content="short request"),
        ],
    )
    before = list(log.content)
    async with summaries.context_summary_request(entity, log):
        assert not summaries.schedule_context_summary(
            entity, log, model="gpt-4.1", api_mode="responses"
        )
    assert log.content == before
    entity.entry.async_create_task.assert_not_called()
    assert not summaries._DEFER_CONTEXT_SUMMARY.get()


async def test_duplicate_summary_schedule_keeps_original_task_and_history():
    manager = DeferredContextSummaryManager()
    content = [
        conversation.SystemContent(content="system"),
        conversation.UserContent(content="older"),
        conversation.AssistantContent(agent_id="agent", content="reply"),
        conversation.UserContent(content="recent"),
    ]
    release = asyncio.Event()

    async def summarize(*_args):
        await release.wait()
        return "summary"

    scheduled = []

    def scheduler(coroutine):
        task = asyncio.create_task(coroutine)
        scheduled.append(task)
        return task

    kwargs = dict(
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-4.1",
        api_mode="responses",
        summarize=summarize,
        scheduler=scheduler,
    )
    assert manager.schedule("conversation", content, **kwargs)
    assert not manager.schedule("conversation", content, **kwargs)
    assert len(scheduled) == 1 and len(content) == 4
    release.set()
    await scheduled[0]
    assert await manager.async_apply("conversation", content)
    assert any("summary" in str(getattr(item, "content", "")) for item in content)


async def test_static_schema_validation_accepts_unspecified_properties_in_dynamic_object(
    hass,
):
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "properties": {"required_count": {"type": "integer"}},
                    "additionalProperties": True,
                }
            },
            "required": ["payload"],
        }
    }
    arguments = {
        "payload": {"required_count": 3, "dynamic": "{{ value }}", "extra": "allowed"}
    }
    before = deepcopy(arguments)
    await integrity.async_validate_static_function_arguments(hass, spec, arguments)
    assert arguments == before


async def test_catalog_reset_discards_update_already_in_bundled_version(
    hass, monkeypatch
):
    manager = models.ModelCatalogManager(hass)
    manager.store = MemoryStore()
    manager.available_catalog = deepcopy(catalog.BUNDLED_CATALOG)
    manager.etag = "old"
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        manager, "_candidate_preserves_saved_requests", AsyncMock(return_value=True)
    )
    publish, sync = Mock(), Mock()
    monkeypatch.setattr(models, "activate_catalog", publish)
    monkeypatch.setattr(models, "sync_all_model_lifecycles", sync)
    result = await manager.async_reset()
    assert result["last_error"] is None
    assert (
        manager.available_catalog is None
        and manager.catalog is None
        and manager.etag is None
    )
    publish.assert_called_once_with(None)
    sync.assert_called_once_with(hass)


async def test_disabled_quiet_hours_reconciliation_removes_remaining_timer(
    hass, monkeypatch
):
    manager = _public_manager(hass)
    unsubscribe = Mock()
    manager._unsubscribers = [unsubscribe]
    monkeypatch.setattr(manager, "_publish_state", Mock())
    await manager.async_reconcile()
    unsubscribe.assert_called_once()
    assert manager._unsubscribers == [] and manager._active is None


def test_new_skill_install_rollback_without_previous_version_removes_candidate(
    tmp_path,
):
    target, backup = tmp_path / "candidate", tmp_path / "absent-backup"
    target.mkdir()
    (target / "SKILL.md").write_text("Candidate")
    SkillManager._rollback_staged_skill_sync(target, backup)
    assert not target.exists() and not backup.exists()


@pytest.mark.parametrize(
    "body", [{"error": "not an object"}, {"error": None}, {"error": []}]
)
def test_malformed_provider_error_body_does_not_hide_explicit_model_error(body):
    error = SimpleNamespace(code="model_not_found", type="", body=body)
    assert provider_errors.provider_failure_category(error) == "model_unavailable"


@pytest.mark.parametrize("matched", [False, True])
async def test_continuation_chain_exhaustion_returns_last_result_or_no_match(
    hass, monkeypatch, matched
):
    rule = local_rule()
    rule["continue_matching"] = True
    rules = await manager(rule)
    if matched:
        monkeypatch.setattr(
            rules_module,
            "_async_evaluate_matched_rule",
            AsyncMock(
                return_value=rules_module.RuleEvaluation(
                    rules_module.RuleMatch(
                        rules_module.validate_rule(rule), "good night", False, 100
                    ),
                    True,
                    "Done",
                    successful=True,
                )
            ),
        )
    result = await rules_module.async_evaluate_rule(
        hass,
        rules,
        rules_module.RequestRuleRuntime(),
        "good night" if matched else "unrelated",
        "session",
        configured_model="gpt-4.1",
    )
    if matched:
        assert result.successful and result.response == "Done"
        rules_module._async_evaluate_matched_rule.assert_awaited_once()
    else:
        assert result is None
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("mode", ["regex", "large"])
async def test_generated_reply_defers_expensive_speech_cleanup_without_changing_history(
    hass, mode
):
    from custom_components.extended_openai_conversation_responses.speech import (
        COMPLETED_SPEECH_EXECUTOR_THRESHOLD,
    )

    agent = object.__new__(conv.ExtendedOpenAIAgentEntity)
    agent.hass = hass
    agent.entity_id = "conversation.agent"
    agent.subentry = SimpleNamespace(
        data={
            "speech_processing_enabled": True,
            "speech_regex_replacements": [{"pattern": "raw", "replacement": "clean"}]
            if mode == "regex"
            else [],
        }
    )
    agent._get_exposed_entities = Mock(return_value=[])
    agent._async_retrieve_memories = AsyncMock(return_value=[])
    agent._async_retrieve_temporary_memories = AsyncMock(return_value=[])
    agent._build_system_prompt = Mock(return_value="system")
    agent._get_function_tools = Mock(return_value=[])
    agent._fire_conversation_finished = Mock()
    text = "raw reply" if mode == "regex" else "x" * COMPLETED_SPEECH_EXECUTOR_THRESHOLD
    log = SimpleNamespace(
        conversation_id="conversation",
        continue_conversation=False,
        content=[
            conversation.SystemContent(content="old"),
            conversation.UserContent(content="request"),
        ],
    )

    async def provider(*_args, **_kwargs):
        log.content.append(
            conversation.AssistantContent(agent_id=agent.entity_id, content=text)
        )
        return False

    agent._async_handle_chat_log = AsyncMock(side_effect=provider)
    user = SimpleNamespace(
        text="request",
        language="en",
        conversation_id="conversation",
        as_llm_context=lambda *_: object(),
    )
    deferred = []
    result = await agent._async_generate_message(user, log, deferred_speech=deferred)
    assert deferred == [(text, agent.subentry.data)]
    assert log.content[-1].content == text
    assert result.response.speech["plain"]["speech"] == text
    agent._async_handle_chat_log.assert_awaited_once()
    agent._fire_conversation_finished.assert_called_once()
