"""Contracts for the manual-only full live OpenAI EOAI journey."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-openai-full-e2e.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_live_openai_full_e2e.py"


def test_full_live_openai_workflow_is_dispatch_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]

    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "release:" not in trigger_block
    assert "github.actor == 'conorod1992'" in text
    assert "OPENAI_API_KEY" in text
    assert "ref: ${{ github.sha }}" in text


def test_full_live_openai_journey_crosses_real_ha_and_provider_boundaries() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "bootstrap.async_setup_hass" in text
    assert "hass.config_entries.flow.async_init" in text
    assert "conversation.async_converse" in text
    assert "return await self._original_send(request, *args, **kwargs)" in text
    assert "self.agent._execute_function_tool = self.execute" in text


def test_full_live_openai_journey_proves_tool_continuation_and_retained_data() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "_TOOL_NAME = \"fetch_live_acceptance_marker\"" in text
    assert "_TOOL_MARKER = \"EOAI_LIVE_TOOL_RESULT_7F3A\"" in text
    assert "trace.tool_executions ==" in text
    assert "_serialized_tool_result_contains" in text
    assert "knowledge_search" in text
    assert "_KNOWLEDGE_MARKER = \"EOAI_LIVE_KNOWLEDGE_CODE_K9Q2\"" in text
    assert "continuation_contains_retained_marker" in text


def test_full_live_openai_cost_is_bounded_and_model_profile_is_validated() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "CONF_MAX_TOKENS: 192" in text
    assert "capability_allowed(model, \"function\"" in text
    assert "reasoning_efforts_for_api" in text
    assert "total_provider_calls" in text
